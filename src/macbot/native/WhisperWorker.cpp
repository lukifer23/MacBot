#include "whisper.h"
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>

// Persistent private stdin/stdout transport. Input: BE uint32 byte count + LE f32 mono.
// Output: BE uint32 byte count + UTF-8 text. Logs only use stderr.
static void send(const std::string &s) {
    uint32_t n = static_cast<uint32_t>(s.size());
    char h[4] = {char(n >> 24), char(n >> 16), char(n >> 8), char(n)};
    std::cout.write(h, 4).write(s.data(), s.size()).flush();
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    auto ctx_params = whisper_context_default_params();
    auto *ctx = whisper_init_from_file_with_params(argv[1], ctx_params);
    if (!ctx) { send("ERROR: model initialization failed"); return 1; }
    send("ready");
    char header[4];
    while (std::cin.read(header, 4)) {
        uint32_t n = 0;
        for (unsigned char c : header) n = (n << 8) | c;
        if (n == 0 || n % 4 || n > 16000 * 120 * 4) break;
        std::vector<float> samples(n / 4);
        if (!std::cin.read(reinterpret_cast<char *>(samples.data()), n)) break;
        auto p = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
        p.n_threads = 4; p.language = "en"; p.no_context = true;
        p.print_progress = p.print_realtime = p.print_timestamps = p.print_special = false;
        if (whisper_full(ctx, p, samples.data(), samples.size()) != 0) { send("ERROR: transcription failed"); continue; }
        std::string text;
        for (int i = 0; i < whisper_full_n_segments(ctx); ++i) text += whisper_full_get_segment_text(ctx, i);
        send(text);
    }
    whisper_free(ctx);
    return 0;
}

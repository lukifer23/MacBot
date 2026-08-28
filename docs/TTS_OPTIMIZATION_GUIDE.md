# Speech implementation

Piper 1.7 and Kokoro ONNX 0.6.1 are explicit local selections. Piper remains available; Kokoro Heart and Michael share a pinned 82M model and voice pack. No backend is substituted on failure. Runtime uses only provisioned files and packaged phonemization resources. Kokoro uses CPU ONNX with four inference threads.

Model text is buffered to sentence boundaries, with a word/clause boundary fallback for long sentences. Decimal numbers and common title abbreviations stay intact across token fragments. The former 100-character split could bisect a word. One ordered playback worker consumes phrases, with a four-item synthesis queue and four 50 ms playback credits. Cancellation invalidates queued audio immediately; an in-flight ONNX inference finishes before the synthesis worker can start its next phrase. This is not an acoustic cancellation guarantee.

Synthesis uses each voice's actual rate (Piper 22.05 kHz, Kokoro 24 kHz); playback is resampled to 48 kHz, separately from 16 kHz microphone capture. A protocol check requires an updated Swift helper. The bounded cache key includes model path, voice ID, speed and text.

On 2026-08-28 a local exploratory short paragraph took 1.62–1.98 seconds to generate in full with Kokoro, producing 7.47–8.73 seconds of audio. These were isolated voice auditions, not first-audio latency or a controlled p95 benchmark. Phrase streaming is measured separately. User voice preference, sustained conversation, echo suppression and the full [verification gates](VERIFICATION.md) remain open.

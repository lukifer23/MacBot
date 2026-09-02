# Speech implementation

The production manifest selects one TTS artifact:
`qwen3-tts-1.7b` with the `qwen-aiden-1.7b` voice. Runtime selection is explicit;
there is no Piper, Kokoro, or smaller-Qwen fallback in the production
installation. Alternative catalog entries are lab inputs and must not remain in
a qualified release generation.

One ordered synthesis path emits generation-bound PCM to Swift, which is the
only released playback owner. Cancellation invalidates queued audio for the
request generation. Capture remains a separate 16 kHz native stream for the
single Parakeet final decoder. There is no Python audio helper to build,
package, supervise, or reconcile.

The 2026-08-29 model screen measured Qwen3-TTS 1.7B Aiden at 142 ms first-chunk
p50 and 159 ms p95 in isolated synthesis. Those are scheduling measurements,
not audible latency or full-stack memory evidence. The selected voice still
requires blinded listener acceptance, first-audible p95 below 250 ms for the TTS
gate, aggregate full-stack RSS below 8 GiB, route-change testing, interruption,
and endurance acceptance. See [MODEL_SCREENING.md](MODEL_SCREENING.md) and
[VERIFICATION.md](VERIFICATION.md).

Historical Piper/Kokoro/helper measurements describe the 2026-08-28 prototype
only. They must not be used to diagnose or qualify the current native/Qwen
release path.

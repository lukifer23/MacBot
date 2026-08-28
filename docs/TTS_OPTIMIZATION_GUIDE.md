# Speech implementation

Piper 1.7 is resident. Synthesis produces incremental audio chunks at the voice's declared sample rate, then resamples for the native engine. One playback worker preserves ordering. Queue capacity and playback credits bound lookahead. Cancellation invalidates a generation and discards stale speech. The bounded cache includes model path, voice selection, speed and text.

Earlier claims about fixed speedups, MPS acceleration, automatic quantization and production readiness were unsupported and have been removed. Measure cold/warm behavior using real speech and record both software scheduling and acoustic playback separately. The [verification gates](VERIFICATION.md) define acceptance.

# Native reliability rebuild checkpoint — 2026-08-29

This is an in-progress recovery checkpoint on `main`, not a hands-free release.
The native application and installed service tree are functional in software;
microphone/speaker, acoustic, voice-listening, and soak acceptance remain open.

## Implemented and verified locally

- SwiftUI conversation, Library, Settings, Diagnostics, menu-bar lifecycle, and
  app-owned AVAudioEngine capture/playback using authenticated private sockets.
- A single supervised assistant pipeline with sequenced events, bounded queues,
  interruption acknowledgements, typed semantic planning, real tool results,
  and per-session encrypted history.
- 16K default context with 70% semantic compaction, source-linked summaries,
  and MiniLM retrieval over older turns.
- SQLite-authoritative documents with a versioned memory-mapped exact index.
  Missing derived files are backed up and rebuilt from authoritative records;
  corrupt or embedding-incompatible indexes still fail closed.
- Exact llama.cpp release `b10509` (`fe8156f789011f6ea0baf6917ea09f88b89d9554`)
  built locally with recorded revision, flags, licenses, and binary hashes.
- Browser diagnostics are disabled by default and share the assistant pipeline
  when explicitly enabled.
- A clean versioned Python 3.12 runtime is built from the wheel and atomically
  selected under `~/Library/Application Support/MacBot/runtime`. The installed
  app contains no checkout path.

The supported non-device suite passes **117 tests**, with five physical-device
tests deselected. Ruff, format, mypy, JavaScript syntax, Swift release build,
wheel inspection, installed-service readiness/shutdown, and ad hoc signature
verification pass. The installed service tree reached ready with about 2.67 GiB
aggregate sampled RSS, then stopped without leaving MacBot processes. The
resolved dependency audit reports no known third-party vulnerabilities after
upgrading `cryptography` to 50.0.1.

Fresh 50-case routing runs on exact llama.cpp b10509 produced 49/50 (98%) for
Qwen3.5-2B Q4_K_M and 47/50 (94%) for Qwen3-1.7B. Qwen3.5 remains selected.
These are software task-selection results, not speech or user acceptance. See
[model screening](MODEL_SCREENING.md) for hashes, latency, and provenance gaps.

## Release blockers

- Integrate and benchmark an Apple-Silicon-local Qwen3-TTS 0.6B candidate, then
  obtain user listening acceptance. Kokoro and Piper remain selectable fallbacks.
- Add truthful partial transcription and complete current Parakeet/Whisper device
  comparison with reproducible recordings.
- Produce the selected Qwen3.5 GGUF through a pinned conversion from official
  source weights; the currently installed candidate is a pinned derived GGUF.
- Run controlled built-in microphone/speaker conversation, overlap/echo checks,
  acoustic latency measurement, interruption measurement, and a 30-minute soak.
- Complete the clean offline installed-wheel, hostile-input/security, visual,
  accessibility, browser fallback, and hosted-CI gates after the remaining code.

Model weights, credentials, documents, recordings, private reports, and runtime
state are not packaged or committed.

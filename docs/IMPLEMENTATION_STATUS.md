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

The supported non-device suite passes **124 tests**, with five physical-device
tests deselected, and the native Swift suite passes **4 tests**. Ruff, format,
mypy, JavaScript syntax, Swift release build,
wheel inspection, installed-service readiness/shutdown, OS-network-denied
installed-wheel verification, and ad hoc signature verification pass. The
installed service tree reached ready with about 2.67 GiB
aggregate sampled RSS, then stopped without leaving MacBot processes. The
resolved dependency audit reports no known third-party vulnerabilities after
upgrading `cryptography` to 50.0.1.

The native app now passes the encrypted-history key only through inherited
private pipes, including a fresh pipe for each supervised assistant restart.
It waits through macOS dark wake instead of failing Keychain access, restarts
its owned service tree after repeated IPC loss, reopens the conversation window
from the menu bar, and renders transcript, response and action events in one
sequence-ordered timeline. The current Mac entered dark wake during the visual
review, so the native screenshot, VoiceOver, microphone and speaker gates remain
open rather than being reported as passed.

Fresh 50-case routing runs on exact llama.cpp b10509 produced 49/50 (98%) for
the earlier Qwen3.5-2B Q4_K_M and 47/50 (94%) for Qwen3-1.7B. The reproducible
official-source Qwen3.5 Q4 then passed 29/30 (96.7%) on the untouched holdout
and all 19 real runtime regression cases. It is now selected.
These are software task-selection results, not speech or user acceptance. See
[model screening](MODEL_SCREENING.md) for hashes, latency, and provenance gaps.

## Release blockers

- Obtain user listening acceptance for the integrated Qwen3-TTS 0.6B and 1.7B
  audition candidates. Kokoro and Piper remain selectable fallbacks.
- Complete the current Parakeet/Whisper device comparison with reproducible
  recordings. Real bounded interim transcription is implemented and shares the
  final turn ID, but spontaneous speech still needs device acceptance.
- Run controlled built-in microphone/speaker conversation, overlap/echo checks,
  acoustic latency measurement, interruption measurement, and a 30-minute soak.
- Complete the remaining hostile-input/security, visual, accessibility, browser
  fallback, and hosted-CI gates. The clean installed wheel passes offline
  service, inference, context, document retrieval, and shutdown verification.

Model weights, credentials, documents, recordings, private reports, and runtime
state are not packaged or committed.

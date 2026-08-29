# Active rebuild checkpoint — 2026-08-28

The last preserved remote baseline is
`67781d56e2e651ded0fafc2593ef6886384cf6df`. The working tree integrates the
unfinished schema/intent work rather than discarding it. MacBot services remain
stopped while the native rebuild is developed; no microphone or playback device
test has been started during this rebuild.

## Implemented in the current foundation slice

- Typed semantic `respond`, `clarify`, and bounded multi-action planning with
  exact source-span authorization and real-result response composition.
- Structured Open-Meteo weather, Keychain-backed Brave search, and a labeled
  DDGS degraded fallback.
- AES-256-GCM SQLite conversation/task/event storage with 30-day retention.
- SQLite-authoritative documents and a versioned exact memory-mapped MiniLM
  vector index; the Chroma runtime dependency and its large transitive graph
  are removed.
- A signed SwiftUI `MacBot.app`, menu-bar lifecycle, single-instance guard,
  document library, diagnostics, transcript/task UI, and authenticated private
  control/audio sockets.
- Native app-owned AVAudioEngine capture and playback with voice processing,
  explicit channel mapping, bounded 16 kHz PCM capture, generation-bound
  playback, mute, stop, and interruption.

## Verified for this slice

- Ruff and mypy pass.
- 74 non-model/non-device Python tests pass.
- 15 real MiniLM retrieval tests pass.
- The Swift release build succeeds; the assembled app passes strict ad hoc
  signature verification and contains microphone usage metadata.

These results do not establish real model routing quality, spontaneous speech
transcription, acoustic echo cancellation, audible latency, voice quality,
single-instance operator behavior, clean external wheel execution, or release
readiness.

## Next gates

1. Push the green foundation checkpoint to `origin/main` after fast-forward
   verification.
2. Finish context compaction, per-session restoration, settings/diagnostics,
   browser fallback isolation, and native playback acknowledgements.
3. Provision and benchmark the exact llama.cpp/model candidates and integrate
   the selected TTS backend without silent fallback.
4. Run package/security/integration checks, then controlled built-in
   microphone/speaker testing, user UI/listening review, and the 30-minute soak.

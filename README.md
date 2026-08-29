# MacBot

MacBot is a private, local macOS voice assistant. The current rebuild makes a
native SwiftUI application the primary interface and keeps the authenticated
loopback dashboard as a diagnostics fallback.

**Modernization is in progress. This checkpoint is not yet a verified
hands-free release.** The native bundle builds and its software gates pass, but
live microphone/speaker behavior, model selection, voice quality, acoustic
latency, and the 30-minute soak still require device and user acceptance.

## Build the native application

Target: Apple Silicon, Python 3.12, macOS 14 or newer, `uv`, Git, CMake, FFmpeg,
and Apple Command Line Tools.

```sh
git clone https://github.com/lukifer23/MacBot.git
cd MacBot
./scripts/bootstrap_mac.sh
uv sync --frozen --all-extras
uv run --frozen macbot setup
uv run --frozen macbot build-inference
uv run --frozen macbot models download qwen3.5-2b-official parakeet qwen3-tts-1.7b minilm silero
./scripts/build_native_app.sh --install
open "$HOME/Applications/MacBot.app"
```

The install build creates a clean Python 3.12 environment from the locked graph,
installs the built wheel under `~/Library/Application Support/MacBot/runtime`,
preserves the prior runtime under `backups` for rollback, and ad hoc signs
`MacBot.app`. The installed application contains no checkout path. A build
without `--install` remains a repository-linked development bundle.

All mutable settings, models, documents, credentials, history, and logs live
under `~/Library/Application Support/MacBot`. Normal operation never rewrites
tracked defaults. Runtime models are provisioned explicitly and must work
offline; a failed backend is never silently replaced.

The selected LLM is built locally from pinned official Qwen source weights with
llama.cpp b10509 and verified against registered F16 and Q4_K_M hashes. Model
conversion occurs only during explicit provisioning. The current Qwen3-TTS
1.7B Aiden voice is an audition candidate, not a listening-approved release
voice.

## Current architecture

The SwiftUI application owns microphone permission, capture, echo-referenced
playback, mute, interruption, the conversation window, and the persistent menu
bar control. It connects to one owned Python service tree through separate
owner-only Unix sockets for control/events and framed PCM. Each launch uses a
single-use 256-bit token. Quitting the app stops only the MacBot-owned service
tree.

The assistant service owns the event journal, conversation context, semantic
planning, bounded tool execution, synthesis scheduling, and cancellation. A
typed plan can respond, clarify, or execute at most four actions. Supported
explicit requests include local time, weather, web search, document retrieval,
opening named applications or URLs, and screenshots. Greetings and ordinary
questions cannot authorize actions. Tool results are executed first, returned
to a response-only model call as untrusted data, displayed in the timeline,
and spoken.

Brave Search uses a credential stored in Keychain. DDGS is an explicitly
degraded no-key fallback. Weather uses structured Open-Meteo results and does
not open a browser. Destructive, file-changing, account-changing, purchasing,
and messaging actions are outside this release.

Conversation messages, tasks, summaries, and event payloads use AES-256-GCM in
SQLite with a Keychain key and 30-day default retention. Raw microphone audio
is not stored. Documents remain authoritative in SQLite; a versioned,
memory-mapped exact vector index uses the same local MiniLM ONNX embeddings for
ingestion and queries. Previous index revisions remain available for rollback.

The browser dashboard is retained for diagnostics and compatibility. It must
never own a second assistant, microphone, playback worker, or history pipeline.

## Verification

```sh
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src/macbot
uv run pytest -m 'not device'
uv run pip-audit --local
uv build && python3 scripts/inspect_package.py
./scripts/build_native_app.sh
```

Device tests are deliberately separate because they open the built-in
microphone and play speech. Missing device authorization is an unrun release
gate, not a passing skip. See [verification](docs/VERIFICATION.md) for the
required command and acceptance thresholds.

## Documentation

- [Architecture and native IPC](docs/ARCHITECTURE.md)
- [Setup and development](docs/DEVELOPMENT.md)
- [Configuration](docs/CONFIGURATION.md)
- [API](docs/API_REFERENCE.md)
- [Security and privacy](docs/SECURITY.md)
- [Migration and rollback](docs/MIGRATION.md)
- [Verification and measured evidence](docs/VERIFICATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Current checkpoint](docs/PAUSED_STATE.md)

Docker files are retained as unsupported legacy material. Containers do not
provide this release's native microphone, speaker reference, Metal, Keychain,
or desktop integration.

## Licenses

MacBot's Python source retains its MIT metadata. Third-party runtimes and model
weights have separate licenses recorded by the model catalog. Piper 1.7 is
GPL-3.0. Review all upstream terms before redistributing any bundle.

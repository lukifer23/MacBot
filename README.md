# MacBot

MacBot is a local AI assistant for Apple Silicon Macs. Talk to it, dictate or transcribe recordings into an editable draft, search your documents, and review research or app-opening proposals. The SwiftUI app runs local speech recognition, model inference, and voice playback through a MacBot-owned runtime.

**Status:** Pre-release. The installed app, model quality, physical audio, accessibility, and soak tests have separate acceptance gates. See the [verification ledger](docs/VERIFICATION.md) for measured results and open checks.

| Where | What you can do |
| --- | --- |
| Conversation | Type or talk, choose spoken replies, interrupt playback, and copy the latest transcript. Dictation creates an editable draft using speech recognition alone. |
| Task Center | Review a research plan and its target scope, authorize or deny it, pause or stop work, and inspect recorded sources and step outcomes. |
| Library | Import and search local PDF, DOCX, or text documents. |
| Mac Actions | Ask the local model to find an app, review its exact target, and open it. Open a chosen app or reveal an item directly. |
| Settings and Diagnostics | Manage voice and context settings, configure optional Brave Search, and inspect service and latency data. |

## Install on an Apple Silicon Mac

You need macOS 14 or newer, Apple Command Line Tools, Git, uv, CMake, and FFmpeg. The project uses Python 3.12.

```sh
git clone https://github.com/lukifer23/MacBot.git
cd MacBot
./scripts/bootstrap_mac.sh
uv run --frozen macbot build-inference
uv run --frozen macbot models download --release
uv run --frozen macbot models verify --release
./scripts/build_native_app.sh --install
open "$HOME/Applications/MacBot.app"
```

The installer activates a paired app and runtime and keeps a rollback generation. Models need an initial download or conversion. Conversation, dictation, document search, and speech playback run locally after setup. Web research uses configured Brave Search or a requested public URL. Raw microphone audio stays out of history. Conversation and Task records use encrypted local storage and a macOS Keychain key. See the [operator guide](docs/USER_GUIDE.md) and [migration and rollback](docs/MIGRATION.md).

## Models

| Role | Selected release artifact |
| --- | --- |
| Language | Official Qwen3.5-2B, locally converted to Q4_K_M GGUF, served by pinned llama.cpp with Metal |
| Speech recognition | Parakeet TDT 0.6B v3 via parakeet-mlx |
| Speech playback | Qwen3-TTS 1.7B Aiden via mlx-audio |
| Document embeddings | MiniLM via ONNX Runtime |
| Voice activity | Silero via ONNX Runtime |

The release manifest selects an artifact for each role. `macbot model-profile`
shows the configured profile and the available evaluation profiles. Profile
changes verify the pinned artifacts first and take effect when services restart.
Use `macbot model-profile release` to return to the release stack.
`macbot doctor` reports installed runtime versions and the artifacts the selected
profile requires. The lab catalog includes Qwen3.5-4B Q4_K_M and Q5_K_M for a controlled comparison. Use [model screening](docs/MODEL_SCREENING.md) and the [model and runtime plan](docs/MODERNIZATION_2026-11.md) for the comparison protocol, provenance, and promotion gates.

## Architecture and development

SwiftUI owns device I/O and operator approvals. The Python runtime owns inference scheduling, the event journal, document retrieval, and research execution. Authenticated local sockets carry commands, events, and framed PCM. Task execution uses persisted authority and single-use capability receipts. The optional browser view is read-only diagnostics.

```sh
make verify
swift test --package-path native/MacBotApp -c release
uv build
uv run --frozen python scripts/inspect_package.py
```

- [Architecture and native IPC](docs/ARCHITECTURE.md) · [API](docs/API_REFERENCE.md) · [Harness](docs/AGENT_HARNESS.md)
- [Development and dependencies](docs/DEVELOPMENT.md) · [Configuration](docs/CONFIGURATION.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Verification](docs/VERIFICATION.md) · [Implementation status](docs/IMPLEMENTATION_STATUS.md) · [Security and privacy](docs/SECURITY.md)
- [Modernization audit and implementation plan](docs/MODERNIZATION_2026-11.md)

MacBot is MIT licensed. Model weights and third-party runtimes have separate licenses recorded in the model catalog.

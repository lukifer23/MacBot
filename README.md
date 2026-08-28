# MacBot

A local macOS voice assistant with a Flask/Socket.IO dashboard, explicit tool approvals, and offline model inference.

**Modernization in progress. Not yet a verified hands-free release.** Native audio, model selection, migration, and packaging must pass the [release gates](docs/VERIFICATION.md) before deployment. Local service startup is not microphone, listening, or latency acceptance.

## Native setup

Target: Apple Silicon, Python 3.12, macOS 14 or newer. Current verification machine: M3 Pro, 18 GB, macOS 27 beta. Other versions are not device-verified. Install `uv`, Git, CMake, FFmpeg, and Xcode Command Line Tools first; the bootstrap script checks these without installing system software unexpectedly.

```sh
git clone --recurse-submodules https://github.com/lukifer23/MacBot.git
cd MacBot
./scripts/bootstrap_mac.sh
uv run --frozen macbot setup
uv run --frozen macbot build-inference --source "$PWD"
uv run --frozen macbot build-audio
uv run --frozen macbot models download qwen3-4b parakeet amy minilm silero
uv run --frozen macbot doctor
```

The existing Qwen3-4B is retained as a registered selection. Candidate benchmarking also supports `lfm-1.2b`, `lfm-2.6b`, `qwen3.5-0.8b`, `qwen3.5-2b`, `lfm-1.2b-mlx`, and `qwen3.5-2b-mlx`. Select the model explicitly in the user configuration; no backend substitutes for a missing model. Install the MLX comparison backend with `uv sync --frozen --all-extras` and retain `--all-extras` on subsequent `uv run` commands when using it.

All mutable settings, models, documents, credentials, and logs live under `~/Library/Application Support/MacBot`. `--config PATH` overrides the configuration location; see [configuration](docs/CONFIGURATION.md). Normal operation never rewrites tracked defaults.

```sh
uv run --frozen macbot start --background
uv run --frozen macbot status
uv run --frozen macbot open
uv run --frozen macbot stop
```

`open` creates a single-use, 60-second login link. Credentials are not placed in query strings or printed to logs. Services bind only to loopback. After login, choose **Start hands-free** to activate native capture, or use browser push-to-talk. The two capture modes are mutually exclusive. Mute disables native input processing; Stop response cancels generation and queued playback. See [security](docs/SECURITY.md) for the trust boundary and microphone privacy limitations.

## Architecture

The assistant service owns turn history, generation, tool policy, cancellation, and one ordered speech stream. Dashboard HTTP routes and authenticated Socket.IO events are adapters to that runtime. Read-only system/document tools run automatically; every desktop or external-search action requires a single-use dashboard confirmation bound to its exact arguments, session, turn, and expiration. Spoken or model-generated text cannot approve an action.

The native Swift helper routes capture and playback through one AVAudioEngine with voice processing. Resident Silero ONNX performs endpointing. STT is explicitly selected between Parakeet MLX and the private persistent whisper.cpp worker. Piper streams synthesis at the voice's actual sample rate. RAG uses CPU MiniLM ONNX for both ingestion and queries; SQLite owns source documents and Chroma holds a replaceable index.

## Documentation

- [Setup and development](docs/DEVELOPMENT.md)
- [Configuration](docs/CONFIGURATION.md)
- [API](docs/API_REFERENCE.md)
- [Dashboard, metrics and live updates](docs/DASHBOARD.md)
- [Agent harness evaluation](docs/AGENT_HARNESS.md)
- [Security](docs/SECURITY.md)
- [Migration and rollback](docs/MIGRATION.md)
- [Verification and measured evidence](docs/VERIFICATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

Docker files are retained as **unsupported legacy material**. Containers do not provide this release's native microphone, speaker reference, Metal, or desktop integration.

## Licenses

MacBot's Python source retains its existing MIT project metadata. Third-party runtimes and weights have separate licenses; the pinned model catalog records their provenance. Piper 1.7 is GPL-3.0. Review upstream model/voice terms before redistributing a bundle. A package or local test does not establish redistribution rights for third-party assets.

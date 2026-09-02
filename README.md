# MacBot

MacBot is a private, local macOS voice assistant. The native SwiftUI application
is the sole operator interface. Its conversation, hands-free controls, Task
Center, local document library, settings, recovery, and diagnostics all reflect
one assistant-owned runtime. The loopback web surface is read-only diagnostics;
it is not an alternate chat, microphone, settings, or action path.

**Modernization is implemented but the product is not yet a verified
hands-free release.** Protocol v3, the durable research loop, native audio
ownership, paired installation, and host-wide model-residency ownership are in
the current source. Scoped selected-model, native-integration, and installed
artifact checks pass. Research-quality, physical audio, listening,
accessibility, endurance, coverage, and final hosted gates remain separate
release evidence.

## Build the native application

Target: Apple Silicon, Python 3.12, macOS 14 or newer, `uv`, Git, CMake, FFmpeg,
and Apple Command Line Tools.

```sh
git clone https://github.com/lukifer23/MacBot.git
cd MacBot
./scripts/bootstrap_mac.sh
uv run --frozen macbot build-inference
uv run --frozen macbot models download qwen3.5-2b-official parakeet qwen3-tts-1.7b minilm silero
./scripts/build_native_app.sh --install
open "$HOME/Applications/MacBot.app"
```

The install build stages one paired app/runtime generation under
`~/Library/Application Support/MacBot/releases`, verifies the wheel, packaged
protocol, executable, and strict ad hoc signature, writes a release manifest,
then atomically switches one `current` generation pointer shared by the stable
app and runtime links. When upgrading an active installation, it quits the
native owner, stops its service tree, activates the pair under the host-wide
inference lease, relaunches the app, and waits for readiness. The previous
verified generation remains available for paired rollback. The installed
application contains no checkout path. A build without `--install` creates a
repository-linked development bundle in the native build cache.

Mutable settings, models, documents, history databases, and logs live under
`~/Library/Application Support/MacBot`. Brave Search and history credentials
live in macOS Keychain. Normal operation never rewrites tracked defaults.
Runtime models are provisioned explicitly and must work offline; a failed
backend is never silently replaced.

The selected LLM is built locally from pinned official Qwen source weights with
llama.cpp b10509 and verified against registered F16 and Q4_K_M hashes. Model
conversion occurs only during explicit provisioning. The production manifest
selects Qwen3-TTS 1.7B Aiden as the sole release TTS artifact. That selection is
not yet listening acceptance.

## Current architecture

The SwiftUI application owns microphone permission, capture, echo-referenced
playback, mute, interruption, conversation and task presentation, settings,
recovery, and the persistent menu-bar control. It exposes five destinations:
Conversation, Tasks, Library, Diagnostics, and Settings. It connects to one
owned Python service tree through independent owner-only command and event
connections plus framed PCM. A long event wait cannot serialize Interrupt,
authorization, Send, or settings. Each launch uses one ephemeral 256-bit token
to authenticate its command, event, and audio connections. Quitting the app
stops only the MacBot-owned service tree.

The assistant service owns the event journal, conversation context, durable Task
engine, capability broker, synthesis scheduling, and cancellation. Conversation
uses one response inference and may add only deterministic read-only enrichment.
An explicit Task persists a bounded plan and authority manifest before native
authorization, then executes its ready planned steps through single-use
receipts, persists observations, and evaluates the accumulated result. It may
finish, block, or request one bounded replan; material replans return to
authorization. Per-observation evaluation, dependency-aware planning, and
claim-linked citation validation remain unfinished Agent Kernel work.

Brave Search uses a credential stored in Keychain. Without a configured
supported provider, web search is unavailable rather than silently substituted.
Weather uses structured Open-Meteo results and does not open a browser.
The first release Task wedge is bounded research over local documents,
configured Brave Search, and bounded web fetch/extraction. Desktop side effects
remain outside this release.
Destructive, arbitrary-write, account-changing, purchasing, and messaging
actions are outside this program.

Conversation messages, tasks, summaries, and event payloads use AES-256-GCM in
SQLite with a Keychain key and 30-day default retention. Raw microphone audio
is not stored. Documents remain authoritative in SQLite; a versioned,
memory-mapped exact vector index uses the same local MiniLM ONNX embeddings for
ingestion and queries. Previous index revisions remain available for rollback.

The browser diagnostics view may report service health and content-free
telemetry when explicitly enabled for development. It remains read-only and has
no fallback assistant, microphone, playback worker, history pipeline, settings
mutation, document mutation, or tool approval path.

## Native product states

MacBot presents one authoritative state across the window and menu bar:
Starting, Ready, Listening, Working, Reconnecting, or Needs attention. Controls
that cannot succeed are disabled. Recovery offers an explicit service retry and
Diagnostics rather than silently ignoring an action. Turn phase remains visible
inside the operational Ready/Listening/Working states.

Research work appears in Tasks and the conversation timeline with its persisted
plan, dependencies, authority, current step, evidence, retry/replan state,
failure class, and terminal result. The composer explicitly separates immediate
Conversation turns from durable Tasks. A new Task shows Planning, then enters
Needs authorization; it does not execute until the user chooses Authorize.
Deny, Pause, Resume, and Stop are shown only in states where protocol v3 permits
them. Clearing a conversation, deleting a document, and removing a search
credential require confirmation.

## Verification

```sh
uv run --frozen ruff format --check src tests scripts
uv run --frozen ruff check src tests scripts
uv run --frozen mypy src/macbot
uv run --frozen pytest -m 'not models and not device and not native_integration'
uv run --frozen pytest -m native_integration --durations=20
uv run --frozen pytest -m 'models and not device'
swift test --package-path native/MacBotApp -c release
uv run --frozen pip-audit --local
uv build
uv run --frozen python scripts/inspect_package.py
./scripts/build_native_app.sh
```

The release ledger separates `software`, `selected-model`,
`native-integration`, `device-audio`, `soak`, and `release-artifact` evidence.
Missing device authorization, an absent listener session, or an unrun installed
artifact check is an open gate, never a passing skip. See
[verification](docs/VERIFICATION.md) for commands and thresholds.

## Documentation

- [Architecture and native IPC](docs/ARCHITECTURE.md)
- [First-run and operator guide](docs/USER_GUIDE.md)
- [Setup and development](docs/DEVELOPMENT.md)
- [Configuration](docs/CONFIGURATION.md)
- [API](docs/API_REFERENCE.md)
- [Read-only browser diagnostics](docs/DASHBOARD.md)
- [Security and privacy](docs/SECURITY.md)
- [Migration and rollback](docs/MIGRATION.md)
- [Verification and measured evidence](docs/VERIFICATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Current implementation status](docs/IMPLEMENTATION_STATUS.md)
- [Historical implementation checkpoint](docs/PAUSED_STATE.md)
- [Agent harness decision](docs/AGENT_HARNESS.md)
- [Model screening](docs/MODEL_SCREENING.md)
- [Dependency audit history](docs/DEPENDENCY_AUDIT.md)
- [Test migration](docs/TEST_MIGRATION.md)
- [Speech model notes](docs/TTS_OPTIMIZATION_GUIDE.md)
- [Modernization overview](docs/ENHANCED_FEATURES.md)

Containers do not form part of the product or verification path.

## Licenses

MacBot's Python source retains its MIT metadata. Third-party runtimes and model
weights have separate licenses recorded by the model catalog and production
manifest. Historical lab candidates do not belong in a release generation.
Review all upstream terms before redistributing any bundle.

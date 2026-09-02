# First-run and operator guide

MacBot is a private Apple Silicon application for macOS 14 or newer. The native
app is the only interactive operator surface. Browser diagnostics are optional,
read-only, and disabled by default.

## Before installation

Install Python 3.12, `uv`, Git, CMake, FFmpeg, and Apple Command Line Tools.
Model provisioning requires several gigabytes of free disk space and must
finish before offline use. The supported production set is one official
Qwen3.5 2B LLM, Parakeet STT, the installed Qwen TTS incumbent, MiniLM, and
Silero. MacBot does not download or substitute a model during inference.

From the repository root:

```sh
./scripts/bootstrap_mac.sh
uv run --frozen macbot build-inference
uv run --frozen macbot models download qwen3.5-2b-official parakeet qwen3-tts-1.7b minilm silero
uv run --frozen macbot doctor
./scripts/build_native_app.sh --install
open "$HOME/Applications/MacBot.app"
```

The installer activates an app and runtime from the same verified generation.
Do not copy either half independently. See [Migration and
rollback](MIGRATION.md) before upgrading an existing installation.

## First launch

Diagnostics should progress from Starting to Ready. If it does not, open the
Diagnostics destination and compare the app, runtime, source, protocol, and
generation identities before retrying services.

MacBot requests microphone permission when hands-free capture is first started.
Allow access in System Settings if you want voice input. Denial does not prevent
typed Conversation or Task use. Raw microphone audio is not retained.

Web research is optional. In Settings, enter a Brave Search API key and save it
to Keychain. Without a configured supported credential, `web_search` returns a
typed unavailable result; MacBot does not open a browser or switch providers.

## Conversation and Tasks

Use Conversation for an immediate, cancellable response. It may use
deterministic read-only enrichment, but it cannot authorize side effects. Choose
whether the reply should be spoken, then send the message. Stop response cancels
the current generation and queued playback.

Use Task for durable bounded research. Submission first creates a persisted
plan and authority manifest. Review its steps, targets, sources, deadline, and
capabilities before choosing Authorize or Deny. Authorized Tasks can use only
local document search, configured web search, and bounded web fetch.

Pause and Stop appear only when legal for the current persisted state. Resume
continues a paused Task. A material replan returns to authorization. An
`unknown_effect` or blocked state requires explicit reconciliation; it is never
silently retried.

The current kernel executes the authorized ready list and then evaluates the
accumulated evidence. Dependency-aware planning, per-step evaluation, and
claim-linked citation validation remain open implementation work, so inspect
evidence provenance before relying on a research result.

## Library, Settings, and diagnostics

Library imports `.txt`, `.pdf`, and `.docx` files into encrypted local storage
and the local exact vector index. Deletion requires confirmation. Imported text
is evidence, never authority.

Settings distinguishes edited, saved, and active values. Changes that require a
restart remain inactive until the controlled service restart completes. The
browser diagnostics page cannot change settings, documents, Tasks, audio, or
conversation state.

Diagnostics reports service readiness, queue and model state, process memory,
protocol and release identity, and recent content-free timing. Readiness does
not prove physical audio, accessibility, research quality, or listener
acceptance; those gates are tracked separately in
[VERIFICATION.md](VERIFICATION.md).

## Stop and remove active model instances

Quit MacBot from the app or menu bar to stop the app-owned service tree. For
maintenance from the repository, use:

```sh
uv run --frozen macbot stop
```

Run direct model benchmarks only after stopping the app and service tree. The
host-wide inference lease prevents supported MacBot supervisors and benchmarks
from owning model residency concurrently; do not work around a lease conflict.

For actionable failures, see [Troubleshooting](TROUBLESHOOTING.md). For release
claims, use the exact gates and evidence boundaries in
[Verification](VERIFICATION.md).

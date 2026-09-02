# Verification and release gates

Release is blocked until every applicable gate below has its own evidence for
the exact final source revision and installed generation. Software, model,
native integration, physical audio, accessibility, listener, soak, hosted, and
artifact evidence are not interchangeable.

## Verification ledger

| Date | Gate | Result | Scope and limits |
| --- | --- | --- | --- |
| 2026-09-01 | Software | **Pass:** 124 passed, 44 deselected; Ruff and format clean; mypy clean for 30 source files | Excludes selected-model, device, and native-integration markers; coverage thresholds remain open |
| 2026-09-01 | Native process integration | **Pass:** 1 passed, 167 deselected | Real macOS service lifecycle and transport scope; no XCUITest, acoustic, visual, or accessibility claim |
| 2026-09-01 | Swift unit/release | **Pass:** 12 passed, 0 failed | Swift package tests only; environment linker search-path warnings remain; no XCUITest, live-app, visual, accessibility, or installed-app claim |
| 2026-09-01 | Selected model | **Partial:** 43 passed, 125 deselected; exactly one production artifact per LLM, STT, TTS, embedder, and VAD role; one observed full production stack used about 1.91 GB aggregate RSS | Real selected-component regressions and one lifecycle census; full research corpus, 4B comparison, acoustic latency, and blinded TTS qualification remain open |
| 2026-09-01 | Installed offline runtime | **Pass for generation `2.1.0-20260901-203051`, source `a5dda9b`:** protocol-v3 streaming chat, prompt accounting, document import/retrieval, reconciliation, encrypted-history plaintext scan, and OS-denied external network | The current installed generation has not repeated this verifier; reconnect, permission, stale-generation, XCUITest, and acoustic paths remain open |
| 2026-09-01 | Paired activation | **Pass for generation `2.1.0-20260901-211451`, source `04a9de7`:** clean-source manifest, strict codesign, app/runtime hashes, one-pointer activation, and ready-stack restoration | Exact-generation offline verifier and paired rollback observation remain open; manifest model values are catalog expectations until installed artifacts are rehashed |
| 2026-09-01 | XCUITest/accessibility | **Open** | Screenshot matrix, keyboard, VoiceOver, large text, narrow window, contrast, and motion not yet recorded |
| 2026-09-01 | Device audio/listener | **Open** | Physical speech, route changes, acoustic interruption/latency, and blinded voice preference not yet recorded |
| 2026-09-01 | Soak/recovery | **Open** | Eight-hour interactive and 24-hour idle/wake runs not yet recorded |
| 2026-09-01 | Hosted/final release | **Open:** hosted run `33579687552` passed source `a5dda9b`; remote `main` now contains later lifecycle and documentation commits | Hosted checks for the eventual final remote revision are not yet recorded |

## Gate commands

### Software

```sh
uv run --frozen ruff format --check src tests scripts
uv run --frozen ruff check src tests scripts
uv run --frozen mypy src/macbot
uv run --frozen pytest -m 'not models and not device and not native_integration'
```

This gate must cover every legal Task transition and the cancel, pause,
deadline, shutdown, and crash matrix at planning, authorization, effect start,
executor return, evaluation, synthesis, and terminal commit. The Task, runtime,
IPC, history, and capability changes require at least 90% changed-line coverage
and 85% branch coverage.

The most recent software-only coverage probe did not satisfy those thresholds:
overall coverage was 49%, with critical branch coverage including native IPC
46%, runtime 33%, Task engine 66%, capabilities 81%, and history 84%. This is an
open harness and implementation-quality gate, not a passing skip.

### Native integration

```sh
uv run --frozen pytest -m native_integration --durations=20
swift test --package-path native/MacBotApp -c release
```

The Python gate starts the real production diagnostics service and exercises
macOS port-reuse and supervisor ownership. Generic restart serialization uses
real owned child processes without repeatedly cold-starting the full service.

### Selected model

```sh
uv run --frozen macbot stop
uv run --frozen pytest -m 'models and not device'
uv run --frozen --all-extras python scripts/benchmark_transcription.py \
  --output /absolute/private/parakeet.jsonl
uv run --frozen --all-extras python scripts/benchmark_models.py qwen3.5-2b-official \
  --case-set holdout --output /absolute/private/qwen35-official.jsonl
uv run --frozen python scripts/benchmark_tts.py qwen-aiden-1.7b \
  --output /absolute/private/qwen-tts-17.json
```

Direct model tests and benchmarks must run with the app and service tree
stopped. The benchmark scripts acquire the host-wide inference lease and must
not coexist with a second LLM, STT, TTS, or embedding stack.

The production census must find exactly one live LLM, Parakeet STT, selected
Qwen TTS, and MiniLM embedder, with no alternative release artifacts and no
more than 8 GiB aggregate RSS. Research acceptance requires at least 95% Task
completion, 100% valid structured decisions, at least 95% supported citations,
at least 95% correct no-answer behavior, and zero unauthorized actions.

The incumbent is `qwen3.5-2b-official`. A 4B comparison may select a locally
converted, checksum-pinned official `Qwen/Qwen3-4B-Instruct-2507` only if it
improves full-trajectory completion or grounded citation accuracy by at least
five percentage points, keeps 100% schema validity and zero unauthorized
actions, stays within 8 GiB full-stack RSS, and increases warm p95 response
latency by no more than 25%. Otherwise the incumbent remains selected.

Qwen3-TTS 1.7B requires at least 70% blinded preference, first-chunk p95 below
250 ms, and full-stack RSS below 8 GiB. Its production-manifest selection is not
listener acceptance and therefore does not yet satisfy the final selection
rule. It is the installed incumbent, not a qualified release choice; complete
the comparison or restore 0.6B before release.

### Native product integration

```sh
swift test --package-path native/MacBotApp -c release
./scripts/build_native_app.sh
```

Run the app against a real isolated runtime. Cover startup reconciliation,
event gaps, epoch changes, reconnect, command acknowledgement below 150 ms while
an event wait is active, Task planning/authorization/progress, material replan,
typed failures, permission denial, stale generation, settings restart, document
import/search, and recovery after every transaction boundary. Deterministic
fault injection may pause real production boundaries; simulated planners and
mocked services cannot support a release claim.

The current Task kernel does not yet meet the target loop in this section. It
executes the authorized initial list before one accumulated-outcome evaluation;
the planner does not encode dependencies, retries have no exponential delay,
and final text has no validated claim-to-evidence citation map. These are open
Agent Kernel gates, not documentation shorthand for completed behavior.

### Release artifact

```sh
uv build
uv run --frozen python scripts/inspect_package.py
uv run --frozen pip-audit --local
./scripts/build_native_app.sh --install
```

The installed verifier now uses authenticated protocol-v3 native IPC rather
than removed browser mutation routes:

```sh
cd /tmp
sandbox-exec -p '(version 1)(allow default)(deny network*)(allow network-bind)(allow network-inbound (local ip "localhost:*"))(allow network-outbound (remote ip "localhost:*"))(allow network-outbound (remote unix-socket))' \
  /absolute/runtime/bin/python /absolute/checkout/scripts/verify_installed_runtime.py \
  --provisioned "$HOME/Library/Application Support/MacBot" \
  --report /absolute/private/new-installed-report.json
```

Verify the exact source SHA, clean/dirty marker, app executable hash, runtime
Python hash, protocol resource, selected model hashes, configuration schema,
installation generation, strict codesign, offline startup, state
reconciliation, single-pointer atomic paired activation, and paired rollback. The verifier
must run outside the checkout, refuse report overwrite, use a fresh inherited
history key, prove the OS blocks external networking, scan durable stores for
test plaintext, and stop only its owned process tree.

Installed verification and release activation use the same per-user host-wide
inference lease as the production supervisor. Stop the active stack before an
isolated verifier run. The installer performs that quiesce automatically and
must restore exactly one ready stack when it upgrades a running generation.

The installer currently retains every historical generation directory even
though only `current` and `rollback` are authoritative. Release acceptance
requires pruning non-authoritative generations after a successful activation,
while never deleting the active or rollback pair.

`web_fetch` validates that a hostname resolves only to public addresses before
each request and redirect, but the HTTP client resolves again when connecting.
Pinning the connection to a validated address while preserving Host and TLS SNI
is an open SSRF gate; the current check does not eliminate DNS rebinding.

### Native product and accessibility

Use XCUITest with the real isolated runtime and capture the state matrix for
Conversation, Tasks, Library, Diagnostics, and Settings. Qualify keyboard-only
operation, VoiceOver announcements, large text, narrow windows, Increase
Contrast, Reduce Motion, permission denial, reconnect, typed actionable errors,
streaming autoscroll, and focus restoration. Source inspection and Swift unit
tests do not satisfy this gate.

### Physical audio and listening

On the target Apple Silicon Mac, run quiet, noisy, spontaneous, echo, barge-in,
and route-change scenarios through the built-in and supported external devices.
Required thresholds are warm p95 speech-end to first audible response at or
below 1.5 seconds and acoustic interruption acknowledgement at or below 250 ms.
PCM scheduling is not audible latency. Validate one final Parakeet transcript,
no durable partial transcripts, no assistant-triggered turns, ordered playback,
mute/stop, route recovery, and zero unauthorized actions.

Run a blinded Qwen TTS listener comparison and record preference separately
from timing. Complete an eight-hour interactive soak and 24-hour idle/wake
recovery. No automated audio fixture replaces hearing and operating the product.

## Historical evidence

The model timings and hashes in [MODEL_SCREENING.md](MODEL_SCREENING.md) are
dated 2026-08-29 evidence. The 2026-08-28 Piper/Kokoro/helper device checks and
the 2026-08-30 protocol-v2, dual-recognizer, 160-test checkpoint applied to
earlier implementations. They remain useful provenance, but they do not qualify
the current protocol-v3, Swift-audio, single-Parakeet, Qwen-TTS generation.

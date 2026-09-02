# Historical MacBot checkpoint — 2026-09-01

This is a resumable implementation snapshot based on `effccd76c0a7bf4843463e1f8dc5cfddc6e9b182`.
It is not a release claim. Durable architecture is documented in
[ARCHITECTURE.md](ARCHITECTURE.md); test truth belongs in the dated ledger in
[VERIFICATION.md](VERIFICATION.md).

This snapshot is intentionally frozen. It predates the later lifecycle,
residency, installed-generation, and verification work summarized in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md). Do not update its counts
or use it as current operational guidance.

## Implemented at this checkpoint

- Native protocol v3 packages one shared Task contract for Python and Swift.
  Independent command and event connections prevent a long event wait from
  blocking Interrupt, Send, authorization, or settings. Atomic `sync`
  reconciliation returns messages, Tasks, the active turn, cursor, and epoch.
- Conversation and durable research Tasks are explicit native modes across five
  destinations: Conversation, Tasks, Library, Diagnostics, and Settings.
- The encrypted Agent Kernel persists plans, dependencies, authority manifests,
  steps, attempts, evidence, capability receipts, evaluations, replans, and
  terminal state. It permits at most 12 executed steps and two replans. Material
  changes return to authorization.
- Cancel, pause, deadline, shutdown, and error resolution use versioned state
  transitions. Capability execution carries request/task/step/attempt identity,
  an absolute deadline, cancellation, and authorization version.
- Release capabilities are exactly `rag_search`, `web_search`, and bounded
  `web_fetch`. There is no shell, arbitrary write, app control, scheduling, MCP,
  messaging, delegation, Pi adapter, or Hermes runtime.
- Messages, Tasks/steps, authority, and evidence are canonical records. Durable
  Task events retain task/revision references and state deltas rather than full
  presentation snapshots. Conversation history and compaction share generation
  ownership, and partial speech is ephemeral.
- Swift is the only released audio transport. One resident Parakeet produces
  final transcription. The production model manifest selects one LLM, STT, TTS,
  embedder, and VAD: Qwen3.5-2B official, Parakeet, Qwen3-TTS 1.7B Aiden,
  MiniLM, and Silero.
- Installation stages and verifies one paired app/runtime generation, writes a
  release manifest, and atomically swaps one `current` generation pointer shared
  by the stable app/runtime links. The prior pair is recorded in `rollback`.
  Browser diagnostics are
  read-only and have no assistant fallback authority.

## Verified on 2026-09-01

- Consolidated non-device gate: **156 passed in 33.87 seconds** with
  `pytest -m 'not device'`. This includes the available selected-model tests but
  not the full model trajectory, census, latency, memory, or listener gates.
- Swift release tests: **11 passed, 0 failed**, with environment linker
  search-path warnings still emitted.
- Ruff is clean. Mypy succeeds for **28 source files**.

The selected-model, live native-integration, installed-artifact, XCUITest,
accessibility, physical-audio, listener, soak, hosted, and final release gates
remain open. No result above substitutes for them.

## Historical checkpoint

The 2026-08-30 snapshot recorded protocol v2, dual interim/final Parakeet paths,
a fixed-plan Task worker, a Python audio helper, and 160 non-device tests. Those
facts describe that earlier tree only. Protocol v3, single-model final STT,
native-only audio transport, dynamic evaluation/replanning, and native installed
verification supersede those implementation details.

## Resume order recorded at this checkpoint

1. Finish the consolidated software/static run after all shared-worktree changes.
2. Run the selected-model research and citation corpus with real production
   components.
3. Build and verify the wheel, native app, release manifest, offline runtime,
   paired activation, and rollback from the exact final revision.
4. Run live native integration, XCUITest/accessibility, physical audio and
   latency, blinded listening, eight-hour interactive soak, and 24-hour
   idle/wake recovery as separate evidence.

Do not add fallback runtimes, duplicate model instances, browser authority,
Hermes, Pi, or mocked release acceptance to close an open gate.

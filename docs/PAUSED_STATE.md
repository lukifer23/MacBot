# MacBot implementation checkpoint — 2026-08-30

This checkpoint is prepared for source-control publication on `main`, based on
`ba039fde5857d8707c2271bfc2583b323cf86225`. It is not a release claim.

## Implemented in this checkpoint

- SwiftUI is the sole operator surface. The browser product path was reduced to
  disabled-by-default, authenticated, read-only developer diagnostics. Browser
  chat, microphone, playback, interruption, clear, settings, document,
  approval, action, and restart routes were removed, along with Socket.IO.
- Conversation and Task are explicit native modes. Conversation uses one response
  inference and only deterministic read-only enrichments. Side effects require
  Task mode.
- A durable encrypted Task kernel now owns canonical task/step states, plans,
  manifests, steps, attempts, capability receipts, idempotency, results,
  provenance, pause/cancel, and startup recovery.
- Task plans and every step are persisted before execution. Each step consumes a
  single-use receipt bound to its capability and normalized arguments. Recovered
  uncertain side effects become `unknown_effect` and block instead of replaying.
- Mixed results remain `partial`. Failure to generate final prose cannot overwrite
  recorded tool truth. Conversation clear preserves the Task ledger.
- Event reads are session-filtered, live token/transcription deltas are not
  synchronously written to encrypted SQLite, per-record retention is enforced,
  native IPC connections close cleanly, and child environments use allowlists.
- Readiness now depends on required native IPC. Service supervision has explicit
  dependency/recovery state and bounded stabilization-aware retry behavior.
- Configuration has one packaged schema and selected Qwen3.5-2B llama.cpp path.
  DDGS/provider substitution, the stale executable config, Docker product path,
  obsolete approval settings, fake TTS benchmark, browser assets, and unsupported
  speech-speed setting were removed.
- `qwen-aiden-1.7b` is the configured release-voice target. It remains blocked on
  listener/device acceptance; no alternate voice silently substitutes for it.
- RAG supports atomic batch add/delete, bounded index revisions, explicit
  `no_answer`, relevance gating, and source provenance. Model files receive full
  SHA-256 attestation before first process use.
- Native Task Center supports proposal hydration, authorization/denial,
  pause/resume/cancel, reconnect reconciliation, explicit UI states, controlled
  restart, destructive confirmations, typed-response speech preference, and
  Library empty/loading/error states.

## Current verification evidence

- Python non-device suite: **140 passed, 5 deselected**.
- Focused model/config/RAG/TTS suite: **43 passed** during implementation.
- Swift release suite: **7 passed** on this checkpoint.
- Native development bundle built; its ad-hoc signature is valid and satisfies
  its designated requirement. Swift emits two known Command Line Tools default
  search-path warnings, while the package's valid explicit Testing paths work.
- Ruff format/check, mypy, and `git diff --check` pass.
- Wheel and source distribution build successfully and package inspection passes.
- `pip-audit --local` reports no known third-party vulnerabilities; the unpublished
  local `macbot` package is correctly listed as unauditable on PyPI.
- The checkout environment imports `macbot`, loads the console entry point, and
  completes `macbot doctor` with `ready_to_start: true`.

These gates are independent of hosted CI, installed-wheel verification, device,
acoustic, listener, visual, accessibility, and operator acceptance.

The checkout environment failure was traced to a macOS `hidden` filesystem flag
on `.venv`, `site-packages`, and the editable `.pth`; Python therefore ignored
the otherwise correct source path. Stale malformed editable artifacts were
removed, the flag was cleared recursively, and the locked package was reinstalled.
No `PYTHONPATH` workaround is used.

## Open gaps and next implementation order

1. Add final-STT priority/cancellation so an obsolete interim transcription can
   never delay the final utterance.
2. Add a shared foreground-priority inference lane. Task planning/final synthesis
   must yield predictably to Conversation, including before the next model call.
3. Complete bounded evaluate/replan behavior. The current Task kernel executes a
   persisted fixed plan; it records a two-replan budget but does not yet perform
   material-scope reauthorization and replanning.
4. Restrict the first released Task wedge to document plus configured Brave
   research. Existing typed side-effect capabilities remain code-registered but
   must not be enabled for release until research recovery, cancellation,
   provenance, and authorization pass end to end.
5. Calibrate the RAG relevance threshold on a fixed corpus to at most 5% false
   positive answers; current threshold behavior is implemented but not accepted.
6. Add crash-boundary, forged-receipt, duplicate-effect, pause/preemption,
   retention, sustained-stream, corpus-growth, and migration evaluation cases.
7. Raise changed-line coverage to 90% and critical runtime/task/broker/persistence/
   IPC/lifecycle/retrieval branch coverage to 80%.
8. Migrate `scripts/verify_installed_runtime.py` from the removed dashboard
   mutation routes to native IPC, then run the complete packaging and
   installed/offline-runtime gates on this exact working tree and hosted CI.
9. Run XCUITest and manual visual, VoiceOver, keyboard, Reduce Motion, Increase
   Contrast, and large-text acceptance.
10. Run real microphone/speaker latency, spontaneous/quiet/noisy/echo/barge-in
    scenarios, release-voice listening approval, cancellation checks, and a
    30-minute soak.

## Resume point

Begin with a clean status/process check. Preserve the current working tree. Run
the environment, static, and non-device gates once, then implement items 1–4 above before
expanding tools or claiming Task readiness. Do not add Hermes, Pi, routing,
multi-agent execution, shell, arbitrary writes, remote MCP, or another provider.

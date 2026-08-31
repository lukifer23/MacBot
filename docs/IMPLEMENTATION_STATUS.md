# Implementation status — 2026-08-30

MacBot now has the intended product boundary and the first durable Task kernel,
but it is not release-ready. The authoritative current handoff is
[PAUSED_STATE.md](PAUSED_STATE.md); the detailed release gates remain in
[VERIFICATION.md](VERIFICATION.md).

## Architecture now present

```text
SwiftUI MacBot.app
  ├── Conversation
  │     └── deterministic read enrichment + one response inference
  └── explicit Task
        └── encrypted durable task engine
              plan → authorize → execute → record → evaluate
                         │
                         ├── single-use CapabilityBroker receipts
                         ├── task/step/idempotency/provenance ledger
                         └── pause / cancel / startup reconciliation

Read-only developer diagnostics
  └── redacted health, versions, service state, and timing evidence
```

Conversation no longer pays an unconditional planner inference. It cannot run
side effects. A native Task persists its plan, authority manifest, and steps
before authorization; every execution passes through the broker. Uncertain
side effects are never retried automatically, and terminal results retain the
actual step outcomes.

## Verified locally in this checkpoint

- 140 Python non-device tests pass; five physical-device tests remain separate.
- Seven native Swift release tests pass.
- Ruff, mypy, and whitespace validation are clean for the implemented tree.
- The native development bundle built and passed ad-hoc signature validation.
- Wheel/source builds and package inspection pass; dependency audit reports no
  known third-party vulnerabilities.
- Direct environment import, console entry-point loading, and `macbot doctor`
  pass without `PYTHONPATH`; doctor reports the provisioned runtime ready to start.

## Not yet verified for release

- Final-STT preemption over obsolete interim inference.
- Foreground-priority model scheduling across Conversation and Task.
- Dynamic bounded replanning with renewed authorization for material changes.
- Full research-Task end-to-end corpus and calibrated no-answer threshold.
- Crash/effect reconciliation and sustained streaming evaluation matrix.
- Required changed-line and critical-branch coverage thresholds.
- Native-IPC migration of the currently stale installed-runtime verifier, then
  fresh wheel/package/offline-installed-runtime evidence for this exact tree.
- Hosted CI for this exact tree.
- Native XCUITest, visual, accessibility, device, acoustic, listener, and
  30-minute soak acceptance.

No software test, hosted run, model benchmark, device check, listening session,
accessibility review, or operator soak substitutes for another gate.

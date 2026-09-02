# Implementation status — 2026-09-01

MacBot now has the intended product boundary, native protocol v3, and a bounded
durable research loop, but it is not release-ready. The authoritative current
handoff is
[PAUSED_STATE.md](PAUSED_STATE.md); the detailed release gates remain in
[VERIFICATION.md](VERIFICATION.md).

## Architecture now present

```text
SwiftUI MacBot.app
  ├── Conversation
  │     └── deterministic read enrichment + one response inference
  └── explicit Task
        └── encrypted durable Agent Kernel
              plan → authorize → execute → observe → evaluate → replan/finish
                         │
                         ├── single-use CapabilityBroker receipts
                         ├── task/step/idempotency/provenance ledger
                         └── pause / cancel / startup reconciliation

Read-only developer diagnostics
  └── redacted health, versions, service state, and timing evidence
```

Conversation no longer pays an unconditional planner inference. It cannot run
side effects. A native Task persists its plan, dependencies, authority manifest,
and steps before authorization; every execution passes through the broker. Only
typed transient read failures may retry within the original deadline. Material
replans return to authorization, uncertain effects never auto-retry, and
terminal results retain the actual observations and evidence.

## Verified locally on the current working tree

- Consolidated non-device gate: **156 passed in 33.87 seconds** with
  `pytest -m 'not device'`. This includes the available selected-model tests but
  not the full model trajectory, census, latency, memory, or listener gates.
- Native Swift release gate: **11 passed, 0 failed**. The build still emits
  environment linker search-path warnings.
- Ruff is clean. Mypy reports success for **28 source files**.

## Not yet verified for release

- Full selected-model research corpus and calibrated no-answer/citation targets.
- Live native integration against the isolated real runtime.
- Wheel, paired installed artifact, offline startup, release-manifest identity,
  single-pointer atomic activation, transactional upgrade, and rollback for
  this exact tree.
- Required changed-line and critical-branch coverage thresholds.
- Hosted CI for the exact final revision.
- Native XCUITest, visual, keyboard, VoiceOver, large-text, contrast, and motion
  acceptance.
- Physical audio, acoustic interruption/latency, listener preference, eight-hour
  interactive soak, and 24-hour idle/wake recovery.

No software test, hosted run, model benchmark, device check, listening session,
accessibility review, or operator soak substitutes for another gate.

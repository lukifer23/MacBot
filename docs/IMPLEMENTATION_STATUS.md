# Implementation status — 2026-09-01

MacBot now has the intended product boundary, native protocol v3, and a bounded
durable research loop, but it is not release-ready. This file summarizes the
current implementation. Dated acceptance evidence and every remaining release
gate are authoritative in [VERIFICATION.md](VERIFICATION.md).

## Architecture now present

```text
SwiftUI MacBot.app
  ├── Conversation
  │     └── deterministic read enrichment + one response inference
  └── explicit Task
        └── encrypted durable Agent Kernel
              plan → authorize → execute list → evaluate → replan/finish
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

## Verified locally on 2026-09-01

- Software gate: **124 passed, 44 deselected** with
  `pytest -m 'not models and not device and not native_integration'`.
- Selected-model gate: **43 passed, 125 deselected** with real provisioned
  production components. This is regression evidence, not the full research
  corpus, 4B comparison, listener, or physical-latency gate.
- Native-integration gate: **1 passed, 167 deselected**. Native Swift release
  tests: **12 passed, 0 failed**.
- Ruff and format checks are clean. Mypy reports success for **30 source files**.
- Installed generation `2.1.0-20260901-203051` at source `a5dda9b` passed the
  offline verifier. Current generation `2.1.0-20260901-211451` at source
  `04a9de7` passed paired activation and strict packaging/codesign, but has not
  repeated the exact-generation offline verifier. The earlier installed check
  covered authenticated protocol-v3 streaming, document import/retrieval,
  reconciliation, encrypted-history plaintext scanning, and OS-enforced
  offline startup. Transactional active-generation replacement restored one
  ready stack; the latest observed full stack used about 1.91 GB aggregate RSS.

## Not yet verified for release

- Full selected-model research corpus, calibrated no-answer/citation targets,
  official 4B comparison, and blinded TTS selection.
- Native product integration through XCUITest, including reconnect, permission,
  stale-generation, visual, keyboard, VoiceOver, large-text, contrast, and
  motion acceptance.
- Paired rollback recovery observed end-to-end from the installed app, plus an
  installed-artifact rerun for the eventual exact release revision.
- Required changed-line and critical-branch coverage thresholds.
- Per-step Task evaluation, dependency-aware planning, bounded exponential
  backoff, contradiction handling, and validated claim-to-evidence citations.
- DNS-rebinding-safe `web_fetch` connection pinning.
- Pruning installed generations down to the authoritative current and rollback
  pair.
- Hosted CI for the exact final revision.
- Physical audio, acoustic interruption/latency, listener preference, eight-hour
  interactive soak, and 24-hour idle/wake recovery.

[PAUSED_STATE.md](PAUSED_STATE.md) is retained only as a historical snapshot of
the earlier `effccd7` tree. It is not current product or verification truth.

No software test, hosted run, model benchmark, device check, listening session,
accessibility review, or operator soak substitutes for another gate.

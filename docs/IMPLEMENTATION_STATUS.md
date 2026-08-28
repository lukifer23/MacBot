# Modernization checkpoint — 2026-08-28

This is an owner-requested remote backup of work in progress, **not an accepted release**. It is being committed directly on main, with no additional branches.

## Implemented and exercised locally

- Python 3.12/uv packaging and pinned dependencies; private mutable state outside the checkout.
- Loopback services, browser sessions/CSRF/Host/Origin checks, authenticated sockets and single-use exact-action approvals.
- Shared streaming runtime, bounded queues, cancellation, resident local inference and a compiled native voice-processing helper.
- Explicit MiniLM ONNX retrieval, authoritative SQLite documents, versioned Chroma indexes, migration backups and rollback.
- Dashboard redesign with live state/metrics, safe document rendering, explicit confirmations and service recovery controls.
- Local screening of Q4 LLMs and real recorded-speech STT comparisons. No final model winner has been accepted.

Checkpoint checks: **67 software/model tests passed in 28.67 seconds**; one physical-device test was explicitly deselected. Ruff, mypy (21 source files), JavaScript syntax and Git whitespace checks passed. The current-tree secret scan reviewed 60 findings, all public provenance hashes or a negative URL-credential test; no live credential was found. The dependency audit has four Chroma advisories with scoped mitigations, documented separately; it is not a clean vulnerability scan.

## Still open

- Context-budget visibility and long-conversation behavior. Current history pruning is not semantic compaction.
- Broader model/tool-selection accuracy. Initial 20-case results did not generalize fully to the additional 30-case screening. Small/faster models are candidates, not proven replacements.
- Final end-to-end offline wheel validation after the latest edits and hosted CI on the pushed commit.
- Built-in microphone/speaker behavior, browser recording, acoustic latency/interruption measurements, a 30-minute conversation soak and user listening acceptance.
- Final model/voice selection, remaining runtime recovery checks and complete release evidence.

Model weights, credentials, user documents, recordings and private test reports are not added by this checkpoint. Read [verification](VERIFICATION.md), [dependency audit](DEPENDENCY_AUDIT.md), [dashboard](DASHBOARD.md) and [harness evaluation](AGENT_HARNESS.md) for scope and limitations.

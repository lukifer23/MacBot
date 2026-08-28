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

The context/cancellation follow-up passed **83 software/model tests in 34.38 seconds**, including 20 repeated real-generation interruptions and a token-overflow history test. One device test remains deselected. Ruff, mypy and JavaScript syntax checks passed. Cancellation now shuts down the stream socket to wake the reader and leaves response cleanup to that reader, avoiding a cross-thread close race observed during testing.

Hosted software CI also passed for `68cf984`. Follow-up verification found and fixed the supervisor's port probe rejecting closed TCP streams in TIME_WAIT; its probe now matches server address-reuse semantics while continuing to reject live listeners. The source archive allowlist is anchored at its root to exclude vendor submodule documents/tests. Offline installed-wheel checks use an OS loopback-only network policy and isolated temporary data; see the reproducible command in [verification](VERIFICATION.md).

The recovery patch passed **84 software/model tests in 47.84 seconds**, with one physical-device test still deselected. Live browser verification confirmed service restart, automatic reconnection, a new conversation epoch and cleared context/turn metrics. During service failures the dashboard now removes stale model/audio telemetry instead of displaying it as current.

## Still open

- Extended long-conversation behavior and semantic compaction. The follow-up context patch exposes the real token budget and preserves complete tool exchanges while pruning old turns; it does not implement semantic compaction.
- Broader model/tool-selection accuracy. Initial 20-case results did not generalize fully to the additional 30-case screening. Small/faster models are candidates, not proven replacements.
- Recheck release artifacts after subsequent changes. The context patch's installed wheel passed an isolated native startup, authenticated dashboard text stream, context metrics and document import/retrieval with external networking denied by macOS. This did not open audio devices. Hosted software CI passed for checkpoint `742e3ef` ([run 33204304226](https://github.com/lukifer23/MacBot/actions/runs/33204304226)); this excludes model inference and physical-device acceptance.
- Built-in microphone/speaker behavior, browser recording, acoustic latency/interruption measurements, a 30-minute conversation soak and user listening acceptance.
- Final model/voice selection, remaining runtime recovery checks and complete release evidence.

Model weights, credentials, user documents, recordings and private test reports are not added by this checkpoint. Read [verification](VERIFICATION.md), [dependency audit](DEPENDENCY_AUDIT.md), [dashboard](DASHBOARD.md) and [harness evaluation](AGENT_HARNESS.md) for scope and limitations.

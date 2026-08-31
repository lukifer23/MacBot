# Test and internal-module migration

The v1 duplicate message buses, conversation manager, sounddevice playback
handler, approval registry, generic resource/error wrappers, and browser operator
adapters are removed from the supported package. Their responsibilities now
belong to `Runtime`, `TaskEngine`, `CapabilityBroker`, `EventJournal`,
`NativeAudio`, `AuthStore`, `DocumentStore`, native IPC, and the owned-process
supervisor. Old internal Python imports are not a supported v2 API. Browser
diagnostics retain only authenticated read-only health evidence.

Test replacements preserve the behavioral concerns rather than mocked implementation details:

| Previous coverage | Replacement |
| --- | --- |
| Config boolean parsing and permissive validation | Explicit configuration precedence, typed validation, rejected legacy/missing configurations and unregistered models |
| Auth token lists / localhost bypass | Real SQLite sessions, service-specific keys, single use, expiry, CSRF, Host and Origin |
| Mocked model loading / dummy vector storage | Real local ONNX and SQLite exact-index implementations; source provenance, CRUD, migration conflict and backup checks |
| Message bus dispatch, reconnect and state locks | Bounded ordered event replay, reconnect gaps, concurrent delivery, close wakeup and real runtime interruption/clear |
| Mocked tool selection / requests | Direct Conversation inference plus durable Task plans, exact session ownership, single-use capability receipts, denial, restart reconciliation and cancellation |
| Fake transcription and concurrent TTS workers | Pinned human recording through both resident recognizers; real Piper output/cache/cancellation and one runtime queue |
| Noisy subprocess stdout | Actual child process writing beyond pipe capacity to owned logs, occupied-port preservation and startup-failure cleanup |
| Tone playback returning booleans | Operator-authorized native capture/playback/cancel assertions; separate acoustic/soak/listening gates |

`pytest -m 'not device'` is the automated software/model subset, not the full release suite. The device test fails with an explicit unmet prerequisite unless the operator authorizes it; it does not skip. No unit result replaces user listening acceptance or the 30-minute soak.

The installed-runtime verifier is not yet migrated from removed dashboard
mutation routes to native IPC; that packaging gate is open and tracked in
[VERIFICATION.md](VERIFICATION.md).

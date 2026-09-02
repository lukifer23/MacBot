# Test and internal-module migration

The v1 duplicate message buses, conversation manager, sounddevice playback
handler, approval registry, generic resource/error wrappers, and browser operator
adapters are removed from the supported package. Their responsibilities now
belong to `Runtime`, `TaskEngine`, `CapabilityBroker`, `EventJournal`,
`AuthStore`, `DocumentStore`, native IPC, Swift audio, and the owned-process
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
| Fake transcription and concurrent speech workers | Pinned human recordings through the single resident Parakeet; real selected Qwen TTS output/cancellation and one runtime queue |
| Noisy subprocess stdout | Actual child process writing beyond pipe capacity to owned logs, occupied-port preservation and startup-failure cleanup |
| Tone playback returning booleans | Operator-authorized Swift capture/playback/cancel assertions; separate acoustic/soak/listening gates |

`pytest -m 'not models and not device and not native_integration'` is the
software gate. `pytest -m native_integration` retains the real macOS service
lifecycle checks as a separately timed gate. Selected-model, device-audio,
soak, and release-artifact gates run
separately. No unit result replaces real model trajectories, XCUITest, physical
audio, user listening acceptance, or endurance recovery.

The installed-runtime verifier now drives authenticated protocol-v3 native IPC,
including atomic `sync`, Conversation, Tasks, settings/status, and document
import/search. Its implementation is present; a fresh installed/offline run on
the exact final artifact is still an open release gate tracked in
[VERIFICATION.md](VERIFICATION.md).

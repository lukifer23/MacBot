# Test and internal-module migration

The v1 duplicate message buses, conversation manager, sounddevice playback handler, generic resource/error wrappers and unused Flask helper are removed from the supported package. Their responsibilities now belong to `Runtime`, `EventJournal`, `NativeAudio`, `AuthStore`, `DocumentStore` and the owned-process supervisor. Old internal Python imports are not a supported v2 API. Public useful dashboard/service features are retained through documented authenticated adapters.

Test replacements preserve the behavioral concerns rather than mocked implementation details:

| Previous coverage | Replacement |
| --- | --- |
| Config boolean parsing and permissive validation | Explicit configuration precedence, typed validation, rejected legacy/missing configurations and unregistered models |
| Auth token lists / localhost bypass | Real SQLite sessions, service-specific keys, single use, expiry, CSRF, Host and Origin |
| Mocked model loading / dummy vector storage | Real local ONNX and SQLite exact-index implementations; source provenance, CRUD, migration conflict and backup checks |
| Message bus dispatch, reconnect and state locks | Bounded ordered event replay, reconnect gaps, concurrent delivery, close wakeup and real runtime interruption/clear |
| Mocked tool selection / requests | Actual llama inference and local RAG service; exact session/turn approval, denial, replay and cancellation |
| Fake transcription and concurrent TTS workers | Pinned human recording through both resident recognizers; real Piper output/cache/cancellation and one runtime queue |
| Noisy subprocess stdout | Actual child process writing beyond pipe capacity to owned logs, occupied-port preservation and startup-failure cleanup |
| Tone playback returning booleans | Operator-authorized native capture/playback/cancel assertions; separate acoustic/soak/listening gates |

`pytest -m 'not device'` is the automated software/model subset, not the full release suite. The device test fails with an explicit unmet prerequisite unless the operator authorizes it; it does not skip. No unit result replaces user listening acceptance or the 30-minute soak.

# Local API v2

Default origins: dashboard `http://127.0.0.1:3000`, assistant `:8123`, RAG `:8001`, supervisor `:8090`, llama `:8080`. Every service is loopback-only. `/health` reports liveness without private state; `/ready` is authenticated. Readiness is not device acceptance.

## Authentication

Use `macbot open` for browser login. `POST /auth/exchange` accepts `{token}` only from the same origin, consumes the token, sets the session cookie and returns `{csrf}`. Browser mutations require `X-CSRF-Token`. `POST /auth/logout` revokes the session. Socket.IO connects with `{csrf}` authentication plus the same session cookie and origin.

Internal service requests use the target service's Bearer credential. These are stored privately, never included in documentation, URLs or query parameters. The dashboard forwards the authenticated session identity to the assistant; callers cannot select another browser's approval session.

## Dashboard adapters

| Route | Method | Payload / result |
| --- | --- | --- |
| `/api/chat`, `/api/llm` | POST | `{message, speak: boolean}` → HTTP 202, `{turn_id, state: accepted}` |
| `/api/voice` | POST | `{audio: base64-or-audio-DataURL}` → accepted turn; STT belongs to the shared service |
| `/api/browser-recording` | POST | `{enabled: boolean}`; exclusive capture lease bound to browser session, with bounded expiry |
| `/api/interrupt` | POST | Cancel active generation, pending approvals and playback |
| `/api/listen` | POST | `{enabled: boolean}`; explicit native capture start/stop |
| `/api/approve` | POST | `{action_id, turn_id, approve: boolean}`; exact session/turn binding, one use |
| `/api/clear` | POST | Interrupt then clear conversation history |
| `/api/preview-voice`, `/api/assistant-speak` | POST | `{text}` → real synthesis/playback turn |
| `/api/events?after=N&epoch=E` | GET | Bounded ordered event replay, epoch, cursor, gap and reset indicators; epoch is optional on the first request |
| `/api/audio-status` | GET | Live PCM peak/RMS, frame age/count, speech detection, capture state and capture/assistant epochs; authenticated, no audio content |
| `/api/status` | GET | Actual assistant state and recent content-free timing records |
| `/api/services`, `/api/metrics` | GET | Owned processes, readiness, restarts and RSS |
| `/api/service/NAME/restart` | POST | Restart only a registered process owned by this supervisor |
| `/api/settings` | GET/POST | Read settings and installed/registered voice lists; update `max_tokens`, `tts_speed`, `tts_voice`; restart required |
| `/api/documents` | GET | Document metadata list |
| `/api/documents/ID` | GET/DELETE | Read/delete source document and rebuild index |
| `/api/search` | POST | `{query, top_k}` → result chunks with source offsets and distance |
| `/api/upload-documents` | POST | Multipart `files`; TXT/PDF/DOCX; report successes and individual failures |

Socket.IO emits `turn_events`. Each event has session ID, turn ID, monotonic sequence, monotonic timestamp, state, kind and data. The containing batch includes the assistant epoch; a new epoch resets replay cursors and pending approvals. State values: accepted, running, completed, interrupted, denied, failed, approval_required. A 202 response is acceptance, not completion. Clients must handle terminal failure, cancellation and replay gaps. No WebSocket message can authorize a mutation.

Errors distinguish invalid input (400), missing authentication (401), denied origin/CSRF/authorization (403), missing record (404), upload failures (422), and unavailable downstream services (503). Request bodies must be JSON objects. Uploaded audio is bounded to 8 MiB decoded data and configured utterance duration. Conversion has a 15-second deadline.

## RAG service

Authenticated `/api/documents` supports GET and POST `{content,title,type,metadata}`. Document GET/DELETE, POST `/api/search` and GET `/api/stats` remain available. Empty search results are a successful empty list; unavailable/index failure is not an empty result. Retrieval text is untrusted tool data.

## Supervisor

Authenticated `/status`, `/services`, `/metrics`, `/ready`, POST `/service/NAME/restart`, and POST `/shutdown`. Status stays HTTP 200 during recovery, with per-service readiness/errors; `/ready` returns 503 until all services are ready. Unknown services are rejected. Occupied ports do not authorize terminating their owners. Shutdown stops only child process groups created by this instance. The CLI waits for observed owned processes to exit before reporting stopped.

# Local API and native protocol v3

Default origins: dashboard `http://127.0.0.1:3000`, assistant `:8123`, RAG `:8001`, supervisor `:8090`, llama `:8080`. Every service is loopback-only. `/health` reports liveness without private state; `/ready` is authenticated. Readiness is not device acceptance.

## Authentication

Use `macbot open` for diagnostics login. `POST /auth/exchange` accepts `{token}` only from the same origin, consumes the token, sets the session cookie and returns `{csrf}`. `POST /auth/logout` revokes the session. The diagnostics service has no conversation, audio, settings, document, approval, action, or lifecycle mutation route.

`GET /auth/session` restores the CSRF token for an already valid HttpOnly session cookie, so a newly opened tab can reconnect without another login code. It does not create or extend sessions. The same Host/Origin/fetch-site checks and no-store responses apply. Missing, expired or revoked cookies return 401. Existing pre-upgrade tab tokens remain valid until their session expires or is revoked.

Internal service requests use the target service's Bearer credential. These are stored privately, never included in documentation, URLs or query parameters.

## Developer diagnostics

MacBot.app is the sole operator client. The authenticated browser page is disabled by default and read-only.

| Route | Method | Payload / result |
| --- | --- | --- |
| `/api/status` | GET | Redacted assistant readiness, active versions, and content-free timing records |
| `/api/services`, `/api/metrics` | GET | Owned processes, readiness, restarts and RSS |
| `/api/pipeline-check` | GET | Redacted supervisor dependency and recovery state |
| `/api/diagnostics` | GET | Combined redacted assistant and supervisor evidence |

The native app uses independent authenticated command and event connections.
Bounded event waits never occupy the command path, so Interrupt, Send,
authorization, settings, and document operations remain responsive. It advances
its cursor only after processing a batch and does not merge a live batch ahead
of historical replay.
Each event has session ID, turn ID, monotonic sequence, monotonic timestamp,
state, kind and data. The containing batch includes the assistant epoch; a new
epoch resets replay cursors and pending work. Conversation states include
accepted, running, completed, interrupted, denied, and failed. Durable Task
authorization uses the canonical Task states below. An
accepted response is not completion. No event transport can authorize a
mutation.

## Native reconciliation and Task contract

Every native connection starts with an authenticated hello for protocol v3.
At startup, reconnect, event-gap detection, or epoch change, the client sends
`{"op":"sync","protocol_version":3}`. The atomic response contains
`protocol_version`, `epoch`, `cursor`, canonical durable `messages`, canonical
`tasks`, and the optional `active_turn`. The client replaces its rehydrated
state from this snapshot before resuming event replay.

The native client continues to accept existing `action`, `tool`, and
`tool_result` journal events. It also accepts a typed `task` event so durable or
multi-step work does not need to masquerade as a one-shot tool:

```json
{
  "kind": "task",
  "state": "running",
  "turn_id": "turn-id",
  "data": {
    "task": {
      "task_id": "task-id",
      "title": "Research local documents",
      "detail": "Searching three indexed sources",
      "source": "explicit_request",
      "commands": ["cancel"]
    }
  }
}
```

The native composer sends immediate turns as
`{"op":"chat","protocol_version":3,"message":"...","speak":true|false}` and durable work as
`{"op":"task_create","protocol_version":3,"message":"..."}`. Task creation returns a persisted
task record in `task`; it is not execution approval. On every service connection
and event-epoch reset, the client reconciles with `sync`. `task_list` remains a
bounded protocol-v3 command for focused diagnostics, not a competing hydration
path.

Canonical task states are `proposed`, `awaiting_authorization`, `queued`,
`running`, `pause_requested`, `paused`, `cancel_requested`, `blocked`,
`completed`, `partial`, `failed`, and `cancelled`. Task protocol v3 also defines
the exact Step states and Failure classes. Live events and list snapshots supply
their exact `commands`:
`awaiting_authorization` permits `authorize|deny`, `running` permits
`pause|cancel`, `pause_requested`, `queued`, and `blocked` permit `cancel`, and
`paused` permits `resume|cancel`. Terminal states permit no command. The native
client sends
`{"op":"task_command","protocol_version":3,"task_id":"...","command":"authorize|deny|pause|resume|cancel"}`
and immediately applies the returned task snapshot. The service remains the
authority and rejects stale, cross-session, invalid-state, and repeated
commands. Missing or incompatible Task protocol versions fail closed.

Errors distinguish invalid input (400), missing authentication (401), denied origin/CSRF/authorization (403), missing record (404), and unavailable downstream services (503). Native commands are authenticated by the private socket token and bound to the single native session.

Every native operation includes `protocol_version: 3`. Supported operations are
`sync`, `status`, `settings`, `update_settings`, `events`, `chat`,
`task_create`, `task_list`, `task_command`, `preview_voice`, `listen`,
`interrupt`, `clear`, `documents`, `document_import`, `document_delete`, and
`document_search`. A successful frame is `{"ok":true,...}`. A failed command is
`{"ok":false,"error":"...","message":"...","failure":{"code":"...","message":"...","retryable":false,"failure_class":"denied|invalid_request|permanent"}}`.
The packaged protocol resource is canonical for Task states and commands; the
remaining operation payloads are still hand-maintained in Swift and Python and
must be schema-generated before claiming a single-source complete protocol.

## RAG service

Authenticated `/api/documents` supports GET and POST
`{content,title,type,metadata}`. Document GET/DELETE, POST
`/api/documents/batch`, POST `/api/documents/batch-delete`, POST `/api/search`,
POST `/api/embed`, and GET `/api/stats` remain available. Empty search results
are a successful empty list; unavailable/index failure is not an empty result.
Retrieval text is untrusted tool data.

## Supervisor

Authenticated `/status`, `/services`, `/metrics`, `/ready`, POST `/service/NAME/restart`, and POST `/shutdown`. Status stays HTTP 200 during recovery, with per-service readiness/errors; `/ready` returns 503 until all services are ready. Unknown services are rejected. Occupied ports do not authorize terminating their owners. Shutdown stops only child process groups created by this instance. The CLI waits for observed owned processes to exit before reporting stopped.

Conversation never authorizes side effects. Research Task proposals are
persisted before execution, approved through the native Tasks destination, and
each ready step consumes a single-use capability receipt. The released
capabilities are `rag_search`, `web_search`, and bounded `web_fetch`; fetched
content is size/content/redirect constrained and recorded as evidence with a
body hash. Material replans require renewed authorization.

The released runtime emits one final `transcription`; it does not emit partial
speech. If a future single-decoder streaming path emits
`partial_transcription`, that event is classified as ephemeral and must never be
written to encrypted history or the durable event journal. Final
`transcription` and `user` presentation share one turn ID and render as one user
message, not duplicates.

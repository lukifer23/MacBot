# Read-only browser diagnostics

The native SwiftUI application is MacBot's only operator interface. The
authenticated loopback page is an optional development diagnostic observer, not
an alternate product surface. It must not expose chat, voice capture, playback,
document mutation, settings mutation, service restart, approval, or action
controls.

The diagnostic page may show only content-free health and performance data:
service process/readiness state, queue depth, dropped-frame counts, model IDs,
memory use, and measured timing summaries. It must distinguish unavailable data
from a real zero or empty result. No model, conversation, task, credential, or
document content is inserted into the page.

## Metrics and freshness

Diagnostics use authenticated HTTP status snapshots. Service process existence
and readiness are separate states; unavailable telemetry is not displayed as an
empty result. Refreshes must be sequential, visibility-aware, and exponentially
backed off during an outage.

Ordered conversation/task replay belongs to the native application. The browser
does not subscribe to conversation events.

- **First response text:** submitted turn to first visible model text. It can include transcription and tool waiting. Recent p95 is descriptive of these completed turns, not the controlled warm benchmark.
- **Speech → playback queued:** last detected speech to first buffer scheduling, or text submission to scheduling for text input. This is **not** the acoustic latency release gate. A dash means no measurement.
- **Process memory:** summed RSS of MacBot-owned service processes and their children, plus the supervisor. Shared mappings may be counted by more than one process; this is not macOS memory-pressure accounting.
- **Queue / dropped frames:** queued turns plus queued synthesis work, followed by dropped capture-frame counts. Audio pipeline details separate capture, model residency and queued playback chunks.
- Last-turn details show STT, first text, TTS first chunk and total duration where measured. They do not fabricate values for unused stages.

The native UI remains a hands-free preview until physical device testing, the
eight-hour interactive and 24-hour idle/wake soaks, accessibility review, and
user listening acceptance pass. A
working diagnostic page is not product acceptance.

Native Diagnostics owns operator recovery. The browser must not restart services
or poll microphone activity. Error presentation must not obscure content or
offer actions that cannot succeed.

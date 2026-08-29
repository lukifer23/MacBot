# Dashboard and live data flow

The authenticated dashboard is an adapter to the assistant, not a second inference pipeline. The assistant owns turns, conversation history, tools and audio. No model or document content is inserted as HTML.

## Main controls

- **Start hands-free** explicitly starts native capture. **Mute** disables captured input. **Stop response** cancels the current turn and invalidates its pending approvals.
- **Record browser voice** releases the native audio device before requesting browser microphone access. Only one capture owner is allowed. Recording is bounded and stops its tracks before upload; failures release the lease.
- Text chat uses Enter to send and Shift+Enter for a newline. Speak replies controls playback for that text turn.
- Supported actions explicitly bound to the current request run once and show requested, running and terminal results without an approval card. Ambiguous or unsupported actions are clarified or denied. Legacy approval events remain fail-closed compatibility data, but the current bounded tool set does not use them as a normal interaction step. Tool payloads are collapsed and rendered as text.
- Documents and settings have separate keyboard-accessible tabs. Import, search, save and restart show pending states and errors. Saving settings does not claim they are active until the assistant restarts.

## Metrics and freshness

Socket.IO carries ordered turn events. HTTP carries authenticated mutations and status snapshots. Visible telemetry refreshes every 2.5 seconds, with a fresh snapshot on returning to the tab. It does not accumulate overlapping polling requests. Service process existence and readiness are separate states; an unavailable service is not displayed as an empty result.

The assistant event stream has an epoch as well as a sequence number. On restart, the browser discards the prior stream cursor and pending approvals. Old responses from an earlier epoch cannot overwrite the restarted conversation. A journal overflow reports a gap. The UI keeps at most 300 rendered messages; the server journal keeps at most 2048 events and runtime timing history at most 256 turns.

- **First response text:** submitted turn to first visible model text. It can include transcription and tool waiting. Recent p95 is descriptive of these completed turns, not the controlled warm benchmark.
- **Speech → playback queued:** last detected speech to first buffer scheduling, or text submission to scheduling for text input. This is **not** the acoustic latency release gate. A dash means no measurement.
- **Process memory:** summed RSS of MacBot-owned service processes and their children, plus the supervisor. Shared mappings may be counted by more than one process; this is not macOS memory-pressure accounting.
- **Queue / dropped frames:** queued turns plus queued synthesis work, followed by dropped capture-frame counts. Audio pipeline details separate capture, model residency and queued playback chunks.
- Last-turn details show STT, first text, TTS first chunk and total duration where measured. They do not fabricate values for unused stages.

The UI remains labeled as a hands-free preview until physical device testing, the 30-minute soak and user listening acceptance pass. Responsive CSS and a working browser are not a substitute for visual/operator acceptance.

The conversation occupies the primary workspace. Performance cards and service controls live in Overview rather than above the conversation. Input level is measured from actual captured PCM, with a separate Silero speech-detected state. While native listening is active and the tab is visible, a bounded sequential audio-status poll runs every 250 ms; it stops on disconnect and does not store audio. The lower-frequency service/turn snapshot remains separate. Errors stay visible in a fixed banner rather than below the fold.

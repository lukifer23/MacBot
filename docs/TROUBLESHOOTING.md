# Troubleshooting

- **MacBot shows Disconnected or Needs attention:** open native Diagnostics,
  inspect the app/runtime/source/protocol generation, then use Retry services.
  Do not start a second runtime or browser assistant as a workaround.
- **No diagnostics login / 401:** run `macbot open` again. Login links expire in
  60 seconds and can be used once. Browser diagnostics are optional and
  read-only; they are not required for native Conversation or Tasks.
- **403 / CSRF / Origin:** use the configured loopback origin directly. Do not
  proxy the diagnostics page or weaken Host, Origin, or CSRF checks.
- **Model missing:** run `macbot doctor`, provision the production roles with
  `make models`, then run `macbot models verify NAME`. The runtime does not
  download or substitute models during inference.
- **`uv run` cannot import `macbot` despite a valid editable install:** inspect
  `.venv`, `site-packages`, and `_editable_impl_macbot.pth` with `ls -ldO`. If
  macOS marked the environment `hidden`, stop project processes, run
  `chflags -R nohidden .venv`, then
  `uv sync --frozen --all-extras --reinstall-package macbot`. Confirm with direct
  import, `macbot --help`, and `macbot doctor`; do not use `PYTHONPATH` to mask it.
- **Service startup failure:** inspect the private log under
  `~/Library/Application Support/MacBot/logs`. An occupied port is reported;
  MacBot does not kill unrelated processes. Restart attempts are bounded.
- **Waiting for this Mac to wake:** macOS can block Keychain access during dark
  wake. MacBot starts no service tree until the Mac is awake and the encrypted
  history key is available. Wake and unlock the Mac; the app retries.
- **Native microphone unavailable:** check microphone permission for MacBot.app.
  Swift owns capture and playback; there is no helper binary to rebuild. Use
  Start hands-free explicitly. Readiness is not proof that physical audio works.
- **Listening, but no final transcript:** inspect native capture/VAD activity in
  Conversation and queue/model state in Diagnostics. MacBot intentionally uses
  one Parakeet final decoder, not a second interim recognizer. Stop and restart
  hands-free capture after a route change, then run the physical device gate if
  the problem persists.
- **Mute indicator remains visible:** input may be muted inside voice processing
  while the device remains open for playback. Stop hands-free mode to close the
  engine.
- **No or poor speech output:** verify `qwen-aiden-1.7b` is installed, inspect the
  native audio route and TTS queue, then run a real preview. Generated PCM and a
  readiness check are not audible or listener-quality evidence.
- **Interrupt is delayed:** capture the native command acknowledgement, event
  cursor, queue depth, and audio generation from Diagnostics. Protocol v3 uses
  independent command and event connections; a 20-second event wait must not
  hold the command path.
- **Task will not run:** open Tasks and inspect its persisted plan, dependencies,
  targets, source scope, authority, deadline, and available commands. A new or
  material replan must be authorized. `unknown_effect` requires explicit
  reconciliation and is never retried automatically.
- **Task capability is unavailable:** the research release contains exactly
  `rag_search`, `web_search`, and `web_fetch`. Shell, arbitrary writes, app
  control, scheduling, messaging, MCP, and delegation are intentionally absent.
- **Empty search:** empty is valid only after a successful search. An unavailable
  service/index is a typed failure. Stop MacBot and use `rebuild-index` after an
  embedding-signature change; back up before migration.
- **State differs after reconnect:** record the displayed epoch and cursor, then
  retry services. Startup, reconnect, gaps, and epoch changes must apply one
  atomic `sync` snapshot of messages, Tasks, active turn, cursor, and epoch.
- **Installed app/runtime mismatch:** inspect
  `~/Library/Application Support/MacBot/releases/<generation>/release-manifest.json`.
  Reinstall with `./scripts/build_native_app.sh --install`; never replace only
  the app or only the runtime.
- **Performance:** retain raw benchmark output and exact model/app/runtime hashes.
  Do not infer latency from GPU use, a single turn, or PCM scheduling. See
  [VERIFICATION.md](VERIFICATION.md) for the separate software, model, native,
  device, soak, and artifact gates.

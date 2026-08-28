# Troubleshooting

- **No login / 401:** run `macbot open` again. Login links expire in 60 seconds and can be used once. Do not put tokens in query strings or weaken authentication.
- **403 / CSRF / Origin:** use the configured loopback origin directly. Do not serve the dashboard through an external proxy. Reopen the dashboard to establish a session and CSRF token.
- **Model missing:** run `macbot doctor`, then explicitly provision the registered model and `macbot models verify NAME`. There is no fallback model or implicit runtime download.
- **Service startup failure:** inspect the corresponding private log in `~/Library/Application Support/MacBot/logs`. An occupied port is reported; MacBot does not kill unrelated processes. Failed startup cleans up owned children. Restart attempts are bounded.
- **Native microphone unavailable:** check macOS microphone permissions for the local helper/launching app. Use Start hands-free explicitly. An AEC capability flag is not proof that the physical microphone and speaker path passed acceptance.
- **Mute indicator:** input is muted inside voice processing, but the device can remain open to support playback; macOS's microphone indicator may remain visible. Stop the service to close the engine.
- **No audio:** check the built-in output device, volume, configured voice and private audio log. Test actual playback; readiness and generated samples are not audible proof.
- **Browser recording:** grant microphone access to the browser and use push-to-talk. Native capture is disabled before browser capture starts. Recordings are limited to configured duration and request size.
- **An action does not execute:** review the pending dashboard approval before expiry. New turns/interruption invalidate it. Model output, documents and spoken phrases cannot authorize it. Disabled tools and unlisted applications remain denied.
- **Empty search:** empty is valid only after a successful search. A unavailable service/index error must be shown as failure. Stop MacBot and use `rebuild-index` when embedding configuration changes. Back up before migration.
- **Performance:** use the reproducible benchmark scripts and retain raw results. Do not infer speed from GPU use, model size, a single turn, or random noise. See VERIFICATION.md for required gates.

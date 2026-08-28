# Verification and release gates

**Release is blocked until all gates below pass.** On 2026-08-28 the owner explicitly requested an earlier in-progress push to `main` to preserve work. Such a checkpoint is a backup of unfinished implementation, not release or device acceptance. The gates below still apply before declaring the modernization complete.

## Automated checks

- Clean uv installation and wheel execution outside the repository.
- Runtime offline after explicit provisioning; no hidden model fetches.
- Configuration precedence, read-only packaged defaults, and private data exclusion.
- Ruff, mypy, full supported tests, dependency audit, secret scan and package inspection.
- Real SQLite/Chroma/ONNX tests; authenticated local service and actual model inference tests. Missing dependencies/models/hardware are failures or explicit unrun gates, never passing skips.
- Authentication/Host/Origin/CSRF/Socket.IO, approval replay/expiry/session binding, disabled tools, malicious document content, invalid uploads and unregistered paths.
- Browser text/PTT, streaming, approvals, interruption, reconnection, voice settings, document CRUD, migration/rollback and owned-process recovery.

## Reproducible model screening

```sh
uv run --frozen --all-extras python scripts/provision_benchmark_audio.py
uv run --frozen --all-extras python scripts/benchmark_transcription.py parakeet --output /absolute/path/parakeet.jsonl
uv run --frozen --all-extras python scripts/benchmark_transcription.py whisper --output /absolute/path/whisper.jsonl
uv run --frozen --all-extras python scripts/benchmark_models.py qwen3-4b --output /absolute/path/qwen3.jsonl
uv run --frozen --all-extras python scripts/benchmark_models.py lfm-1.2b --output /absolute/path/lfm.jsonl
uv run --frozen --all-extras python scripts/benchmark_models.py qwen3.5-2b --output /absolute/path/qwen35.jsonl
uv run --frozen --all-extras python scripts/benchmark_models.py qwen3.5-2b-mlx --backend mlx --output /absolute/path/qwen35-mlx.jsonl
```

Run candidates sequentially with unrelated workloads stopped. Retain raw outputs, model hashes, runtime versions, cold load and first inference separately from warm observations. The ASR subset consists of 20 real LibriSpeech recordings and transcripts, one speaker, <=12 seconds; it is not representative of all accents or conversation. Synthetic Piper audio is useful for integration checks only.

The initial 20-case LLM screening checks ordinary answers and tool selection without executing actions. It does not establish broader model accuracy or prove a latency release gate. Keep candidate selection explicit until quality, memory and cancellation pass.

See [measured model screening](MODEL_SCREENING.md) for the latest configuration and its limitations.

## Installed wheel with external networking blocked

Build and inspect with `uv build` and `uv run --frozen python scripts/inspect_package.py`. Export the pinned runtime graph with `uv export --frozen --all-extras --no-dev --no-emit-project --output-file /tmp/macbot-runtime-requirements.txt`. Create a separate Python 3.12 environment outside the repository and install that requirements file plus the wheel using `uv pip install --offline --python /absolute/environment/bin/python`. For the wheel installation, use `--no-deps`; the exported graph supplies dependencies. A missing cached dependency is a failed offline installation, not a skip.

On macOS, run the verification script with that environment's Python and a working directory outside the checkout:

```sh
cd /tmp
sandbox-exec -p '(version 1)(allow default)(deny network*)(allow network-inbound (local ip "localhost:*"))(allow network-outbound (remote ip "localhost:*"))' \
  /absolute/environment/bin/python /absolute/checkout/scripts/verify_installed_runtime.py \
  --provisioned "$HOME/Library/Application Support/MacBot" \
  --report /absolute/private/report-directory/new-wheel-report.json
```

The script verifies that imports come from the installed environment and that the OS denies an external connection. It creates isolated temporary configuration/documents and distinct loopback ports, shares only provisioned model/binary files, starts all services, authenticates through the dashboard, streams actual model output, checks context metrics and imports/retrieves a real text document. It terminates its owned supervisor afterward. Reports refuse overwrite. It does not open the microphone/speakers, measure acoustics or satisfy listening acceptance.

## Device and listening acceptance

Focused check on 2026-08-28: the previous helper reproduced Core Audio `-10875` on this Mac because its input/output client sample rates differed. After explicitly matching those rates, both device regression cases passed (direct capture start and muted start followed by capture), including real captured frames, Piper playback scheduling and cancellation acknowledgment. Start hands-free was also exercised successfully in Chrome with voice processing enabled, then capture was stopped. The separate software suite passed 84 tests. These checks do not measure acoustic latency, validate transcription of spontaneous speech, establish feedback suppression or replace the soak/listening gates below.

On this M3 Pro with built-in microphone and speakers, use real recorded conversational prompts and overlap user speech with assistant playback. Check microphone ownership, echo suppression, no assistant-triggered turns, ordered playback, final STT tail flushing, Stop/Mute, reconnect, interruption recovery and degraded services.

Required acceptance: warm p95 speech-end to first audible response <=1.5 s; p95 interruption to playback stop <=250 ms; >=95% correct task/tool selection; zero unapproved actions; <=8 GB aggregate process RSS during the standard workload. Within 5% of latency, prefer lower memory.

**Scheduling a PCM buffer is not audible playback.** `first_audio_scheduled_ms` is a software diagnostic, not the latency acceptance measurement. The native stop acknowledgement is also distinct from a measured acoustic stop.

Complete a 30-minute sustained conversation soak and obtain user listening acceptance. Do not label hands-free support complete before that. Record local automated, hosted-CI, and physical-device results separately. Preserve every unmet gate in the delivery report.

### Follow-up microphone and voice repair, 2026-08-28

The owner reported that Start succeeded but speaking produced no response. Direct device diagnostics found nonzero 9-channel native input but all-zero converted mono PCM. Explicit `channelMap = [0]` restored the signal. Both updated device assertions failed against the previous helper and passed with the mapping fix. The final protocol-2 device checks passed four cases: direct/muted starts with Piper and Kokoro, nonzero finite capture, reported input telemetry, playback scheduling and cancellation acknowledgement. They still do not prove spontaneous-speech transcription or acoustic echo suppression.

The software suite passed 88 tests before the final capture-epoch ordering guard; Ruff, formatting, mypy (21 modules) and distribution inspection passed. Kokoro model/voice hashes were verified. Real synthesis from the installed runtime also passed with all networking denied by the OS. The dependency scan still reports the same four Chroma advisories; no new package advisory was reported. The changed-file secret scan flagged public upstream revision/model hashes, not credentials.

A live text-to-spoken-reply turn completed with Kokoro Heart, zero dropped frames, 762 ms to first text and 2,608 ms to first scheduled audio. This was a single cold exploratory turn, not a warm p95 result. The owner confirmed hearing the reply, but did not accept voice quality or sustained conversation. The 1.5-second speech-end acoustic target remains unproven.

Chrome displayed `ERR_BLOCKED_BY_CLIENT` before loading the dashboard, although one listener per configured port and a local HTTP 200 response were observed. Revised UI layout, live input feedback and browser interactions are **not visually/operator verified** in that blocked browser. Do not treat source changes or backend checks as UI acceptance. Any push is an unfinished-work checkpoint, not a release.

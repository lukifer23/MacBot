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

## Device and listening acceptance

On this M3 Pro with built-in microphone and speakers, use real recorded conversational prompts and overlap user speech with assistant playback. Check microphone ownership, echo suppression, no assistant-triggered turns, ordered playback, final STT tail flushing, Stop/Mute, reconnect, interruption recovery and degraded services.

Required acceptance: warm p95 speech-end to first audible response <=1.5 s; p95 interruption to playback stop <=250 ms; >=95% correct task/tool selection; zero unapproved actions; <=8 GB aggregate process RSS during the standard workload. Within 5% of latency, prefer lower memory.

**Scheduling a PCM buffer is not audible playback.** `first_audio_scheduled_ms` is a software diagnostic, not the latency acceptance measurement. The native stop acknowledgement is also distinct from a measured acoustic stop.

Complete a 30-minute sustained conversation soak and obtain user listening acceptance. Do not label hands-free support complete before that. Record local automated, hosted-CI, and physical-device results separately. Preserve every unmet gate in the delivery report.

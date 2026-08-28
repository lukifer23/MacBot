# Local model screening — 2026-08-28

These are actual llama.cpp v0.3.0 runs on the M3 Pro, not vendor benchmark claims. The registry pins each GGUF revision and hash. The `context-v4` reports use the same MacBot system prompt, updated tool descriptions, temperature 0, 4,096-token context and 128-token output cap. Each suite ran twice; the table uses the second pass.

| Model | Quantization | Core (20) | Additional cases (30) | Warm p95 first output, core / additional | Maximum sampled process RSS |
| --- | --- | --- | --- | --- | --- |
| Qwen3-1.7B | Q4_K_M | 19/20 | 28/30 | 151 / 155 ms | 1.77 GB |
| Qwen3.5-0.8B | Q4_K_M | 19/20 | 28/30 | 112 / 112 ms | 0.90 GB |
| Qwen3.5-2B | Q4_K_M | 20/20 | 30/30 | 307 / 359 ms | 1.66 GB |

Qwen3.5-2B is the leading candidate under this configuration. The smaller models missed enough selections to remain below 95% over the combined 50 cases. The packaged default is not changed until broader quality and device acceptance. Qwen3-1.7B is not Qwen3.5-1.7B and is not Qwen3-ASR-1.7B.

## Limits and reproducibility

- First output means a streamed text or tool-call fragment. It is not first audible speech, completed answer time, or a guarantee that the fragment is useful.
- No proposed tool executed in these screens. Approval safety has separate integration tests.
- The additional suite was initially a holdout, but its failures informed the general web-search tool description. These reruns are regression evidence, not an untouched test set. Fifty cases do not establish broad assistant accuracy.
- Runs were sequential without another MacBot model workload. Other desktop activity and thermal state were not controlled. Do not compare earlier runs with different tool schemas as an isolated model improvement.
- RSS sums the benchmark Python process and llama server at case boundaries. It is not peak GPU/unified-memory accounting or the full voice stack's memory gate.
- Raw JSONL and summaries remain in the private data directory's `reports` folder, named `<model>-<core|holdout>-context-v4.*`. Summaries record artifact hashes, runtime versions, binary hash, prompt/schema hashes and settings; raw rows retain every response, selection and timing. Failed cases are not omitted.

Run `scripts/benchmark_models.py MODEL --case-set core|holdout --output /new/report/path.jsonl` using `uv run --frozen --all-extras python`. Reports refuse overwrite. For the next release decision, add unseen conversational and adversarial cases, long context, recorded speech, cancellation, and operator listening tests.

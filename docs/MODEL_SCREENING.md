# Local model screening — 2026-08-29

These are actual runs on this M3 Pro using the source-built llama.cpp **b10509**
binary (`fe8156f789011f6ea0baf6917ea09f88b89d9554`, installed
`llama-server` SHA-256
`bef7e191773c494a259b5b85d4b981e4c9a792e1f078cdf4a6c75be07e3804a8`).
Both candidates used Q4_K_M weights, the same current MacBot prompt and tool
schema, temperature zero, a 16,384-token context target, and a 128-token output
cap. Each suite ran twice; the table reports the warm pass.

| Model | Core | Additional cases | Combined selection | Warm p95 first output, core / additional | Maximum sampled process RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5-2B | 20/20 | 29/30 | 49/50 (98%) | 193 / 195 ms | 1.79 GB |
| Qwen3-1.7B | 19/20 | 28/30 | 47/50 (94%) | 143 / 144 ms | 3.16 GB |

Qwen3.5-2B remains the selected default because it passes the 95% routing gate;
Qwen3-1.7B does not. The smaller model's text onset was faster, but it missed
more actions and used more sampled RSS at this context size. Qwen3-1.7B is not
Qwen3.5-1.7B and is not Qwen3-ASR-1.7B.

## Provenance and limits

- The installed Qwen3.5 file is the pinned Unsloth Q4_K_M artifact derived from
  the official `Qwen/Qwen3.5-2B` model. Its exact revision and file hash are in
  the catalog and receipt. A locally reproduced conversion from pinned official
  source weights is still a release gate; this report does not mislabel the
  community GGUF as an official Qwen artifact.
- First output means the first streamed text or tool-call fragment. It is not
  first audible speech, completed answer time, or proof that the fragment is
  useful.
- No proposed desktop action executed in these screens. Real runtime tests cover
  the typed planner, tool-result response phase, compaction, interruption, and
  required regressions without substituting a fake inference backend.
- The additional suite was initially a holdout, but earlier failures informed
  the general web-search description. These reruns are regression evidence, not
  an untouched benchmark.
- RSS sums the benchmark Python process and llama server at case boundaries. It
  is not peak unified-memory accounting or the full voice stack's memory gate.
- Raw immutable JSONL and summaries are retained under the private reports
  directory with `b10509-16k` in their names. Summaries include artifact,
  binary, prompt, schema, settings, runtime, timing, and RSS provenance.

Run candidates sequentially with unrelated workloads stopped:

```bash
uv run --frozen --all-extras python scripts/benchmark_models.py qwen3.5-2b \
  --case-set core --output /new/private/report.jsonl
```

Reports refuse overwrite. Device speech latency, acoustic echo behavior, broad
assistant quality, and user listening acceptance remain separate gates.

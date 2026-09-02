# Local model screening — 2026-08-29

This file preserves dated benchmark evidence. The 2026-09-01 production manifest
selects one artifact per role: `qwen3.5-2b-official`, Parakeet,
Qwen3-TTS 1.7B Aiden, MiniLM, and Silero. It does not load alternative models as
fallbacks. A future 4B LLM comparison must use a locally converted,
checksum-pinned official `Qwen/Qwen3-4B-Instruct-2507` artifact and the complete
trajectory gates in [VERIFICATION.md](VERIFICATION.md), not the older third-party
GGUF. No 4B selection claim has been made.

These are actual runs on this M3 Pro using the source-built llama.cpp **b10509**
binary (`fe8156f789011f6ea0baf6917ea09f88b89d9554`). The model screens used
`llama-server` SHA-256
`bef7e191773c494a259b5b85d4b981e4c9a792e1f078cdf4a6c75be07e3804a8`.
The current UI-free build from the same revision is
`6f4e8b14e7d9cd47845d8818173dcb007bd0a1d602a278a54962c9190fb9b40c`;
the complete 19-case real runtime suite and the offline installed-wheel test
were rerun after that binary was installed.
Both candidates used Q4_K_M weights, the same current MacBot prompt and tool
schema, temperature zero, a 16,384-token context target, and a 128-token output
cap. Each suite ran twice; the table reports the warm pass.

| Model | Core | Additional cases | Combined selection | Warm p95 first output, core / additional | Maximum sampled process RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5-2B | 20/20 | 29/30 | 49/50 (98%) | 193 / 195 ms | 1.79 GB |
| Qwen3-1.7B | 19/20 | 28/30 | 47/50 (94%) | 143 / 144 ms | 3.16 GB |

The community-quantized Qwen3.5-2B passed the earlier 50-case comparison. The
official-source conversion then passed 29/30 (96.7%) on the untouched 30-case
holdout at 16K: 194 ms warm p95 first output and 1.78 GB maximum sampled
benchmark RSS. The one miss was an unnecessary web-search proposal for a static
general-knowledge question in the raw tool-enabled probe. MacBot's actual typed
planner does not expose tools during the response phase; its complete 19-case
real runtime regression suite passes with the official quant, including
greetings, local time, document-only search, compound apps, and compaction.

Qwen3.5-2B remains the selected default because it passes the 95% routing gate;
Qwen3-1.7B does not. The smaller model's text onset was faster, but it missed
more actions and used more sampled RSS at this context size. Qwen3-1.7B is not
Qwen3.5-1.7B and is not Qwen3-ASR-1.7B.

## Provenance and limits

- The selected `qwen3.5-2b-official` artifact was reproduced locally from
  `Qwen/Qwen3.5-2B` revision
  `15852e8c16360a2fea060d615a32b45270f8a8fc`. llama.cpp b10509 produced the
  registered F16 intermediate and Q4_K_M output. The selected Q4 is
  1,312,164,736 bytes with SHA-256
  `9a766254d3d0b309b199a39a67e6519c66ab963c40b8564ca6baf40a0f5cf5bf`.
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
uv run --frozen --all-extras python scripts/benchmark_models.py qwen3.5-2b-official \
  --case-set core --output /new/private/report.jsonl
```

Stop the active MacBot app and service tree before starting a direct benchmark;
the benchmark acquires the same host-wide inference lease and must never run
beside another model stack. Reports refuse overwrite. Device speech latency,
acoustic echo behavior, broad
assistant quality, and user listening acceptance remain separate gates.

## Qwen3-TTS audition candidates

Official Qwen CustomVoice source checkpoints were converted with pinned
`mlx-audio 0.5.0`, 4-bit affine quantization, and group size 64. Eight real
conversational and pronunciation passages were synthesized per resident model.

| Candidate | Cold load | First-chunk p50 / p95 | Maximum sampled process RSS |
| --- | ---: | ---: | ---: |
| Qwen3-TTS 0.6B Aiden | 1,109 ms | 111 / 133 ms | 1.89 GB |
| Qwen3-TTS 1.7B Aiden | 873 ms | 142 / 159 ms | 2.50 GB |

These are synthesis scheduling measurements, not audible latency or voice
quality. WAV outputs and immutable JSON reports are retained in the private
MacBot reports directory. The production manifest selects Aiden 1.7B as its
sole TTS artifact. Blinded listener acceptance, full-stack RSS, physical
first-audio latency, and endurance gates remain open, so this is not a voice
quality claim.

# Configuration

Python 3.12 and `uv.lock` are authoritative. Start with `macbot setup`; this creates private mutable state under `~/Library/Application Support/MacBot` (directory mode 0700; configuration and credentials 0600). Packaged defaults are reference material, never a write target.

Precedence: validated defaults → `--config PATH`, otherwise `MACBOT_CONFIG`, otherwise the data directory's `config.yaml` → nested `MACBOT__SECTION__FIELD` overrides. `MACBOT_DATA_DIR` overrides a configured data directory. Relative `data_dir` values resolve against the configuration file's parent. Explicit missing files and legacy unversioned configuration fail rather than silently using defaults.

Examples:

```sh
uv run --frozen macbot --config /absolute/path/config.yaml doctor
MACBOT__MODELS__MAX_TOKENS=128 uv run --frozen macbot start
```

Version 2 settings include:

| Section | Settings |
| --- | --- |
| `services` | Dashboard 3000, assistant 8123, RAG 8001, supervisor 8090. Loopback only, distinct ports. |
| `models` | Registered LLM name, `llm_backend` (`llama` or `mlx`), local LLM URL, context length, maximum output tokens, temperature, threads, STT (`parakeet` or `whisper`), voice (`amy`), TTS speed. |
| `audio` | Endpoint silence 350 ms, pre-roll 256 ms, speech start 96 ms, maximum utterance 30 seconds, idle capture timeout 300 seconds, VAD threshold 0.5. |
| `tools` | Enabled tool names, allowed applications, screenshot directory, approval lifetime (60 seconds), and `auto_run_requested` (default `false`). Set `true` to execute supported explicit requests without confirmation; restart the assistant to apply. |

Existing Qwen3-4B remains the default until full candidate acceptance. Change `models.llm` explicitly to compare `lfm-1.2b` or `qwen3.5-2b`. MLX names have `-mlx` suffix and require `llm_backend: mlx` and the `mlx` extra. Selecting a missing backend/model fails; no substitution or runtime downloads occur.

`qwen3-1.7b` selects the pinned Unsloth Q4_K_M quantization of **Qwen3-1.7B**, not a Qwen3.5 or ASR checkpoint. Provision it explicitly with `macbot models download qwen3-1.7b`; the registry records the upstream commit, file hash and license. Its availability does not establish it as a better default.

## Conversation context

The default context budget is 4,096 tokens, including the system prompt, tool schemas, history and reserved reply tokens (`models.max_tokens`). The llama backend counts the actual server-rendered chat template; MLX uses its loaded tokenizer. The dashboard reports prompt tokens, reply reserve, configured limit and turns pruned in the latest request.

Completed tool calls and their original results remain in conversation history as assistant/tool messages. When the budget is exceeded, the oldest complete user turn and its tool exchanges are removed together. The current turn is never partially truncated; a current turn that cannot fit fails explicitly. Clearing the conversation removes history and context metrics. Cancellation prevents late history writes from restoring cleared turns.

This is **whole-turn pruning, not semantic compaction or durable memory**. Older facts can be forgotten; the visible transcript is not proof that a fact remains in model context. No generated summary is promoted to system instructions, and history never grants action approval. Larger context windows and semantic compaction still require long-conversation quality, latency and memory evaluation before changing defaults.

Dashboard changes support output length, voice and speech speed. They save validated user settings and require an assistant restart. Other settings require editing the user configuration and restarting MacBot. `start` persists the effective configuration so child services agree.

Mute uses AVAudioEngine's voice-processing input mute. The audio device may remain open while playback continues, so macOS may still show its microphone indicator. Browser push-to-talk stops native capture before opening the browser microphone and closes browser tracks after recording. Full device closure occurs on service stop.

## Voice choices

Piper voices `amy` and `lessac` remain available. To try Kokoro locally, run `macbot models download kokoro` and `macbot models verify kokoro`, then select `kokoro-heart` or `kokoro-michael` in Settings. The two voices share one resident 82M model. Start at speed `1.0`; no voice-quality claim replaces listening acceptance. Missing models fail explicitly. Uninstalled voices are disabled in the dashboard selector.

After updating from the earlier audio helper, run `macbot build-audio` and restart MacBot. Native IPC protocol 2 uses 16 kHz capture and 48 kHz playback so the voice is no longer downsampled to the STT input rate. The Python bridge rejects an outdated helper with a rebuild instruction. Roll back code, wheel and helper together; voice rollback is selecting a provisioned Piper voice and restarting the assistant.

## Request routing

Direct requests such as “Open Calculator”, “Search the web for sourdough recipes”, “Take a screenshot”, and “What time is it?” select only their relevant tool. Tool output and prior turns cannot select tools for the current turn. Ambiguous follow-ups such as “do that again” require an explicit restatement; compound or unrecognized requests may need to be split. Unsupported file operations remain unavailable. Add `local_time` to `tools.enabled` in an older configuration to enable local clock answers; no network is needed.

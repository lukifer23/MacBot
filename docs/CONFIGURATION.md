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
| `models` | Registered LLM name, `llm_backend` (`llama` or `mlx`), local LLM URL, context length, maximum output tokens, temperature, threads, STT (`parakeet` or `whisper`), registered voice, TTS speed. |
| `audio` | Endpoint silence 350 ms, pre-roll 256 ms, speech start 96 ms, maximum utterance 30 seconds, idle capture timeout 300 seconds, VAD threshold 0.5. |
| `tools` | Enabled tool names, allowed applications, screenshot directory, and legacy approval lifetime. Supported planner actions are automatically executed only after exact current-request authorization. |
| `privacy` | Encrypted history enablement and retention in days (default 30). The encryption key is held in Keychain. |

`qwen3.5-2b-official` is the selected registered LLM. Its catalog entry contains
the official source revision and source-file hashes plus the pinned llama.cpp
b10509 F16 and Q4_K_M conversion hashes. Change `models.llm` explicitly to compare another
registered candidate. MLX names have `-mlx` suffix and require `llm_backend:
mlx` and the `mlx` extra. Selecting a missing backend/model fails; no
substitution or runtime downloads occur.

`qwen3-1.7b` selects the pinned Unsloth Q4_K_M quantization of **Qwen3-1.7B**, not a Qwen3.5 or ASR checkpoint. Provision it explicitly with `macbot models download qwen3-1.7b`; the registry records the upstream commit, file hash and license. Its availability does not establish it as a better default.

## Conversation context

The configured context target is 16,384 tokens, including the system prompt,
history and reserved reply tokens (`models.max_tokens`). The llama backend counts
the actual server-rendered chat template; MLX uses its loaded tokenizer. The
diagnostics view reports prompt tokens, reply reserve, configured limit and turns
pruned in the latest request.

Completed user and assistant messages are stored per conversation. Structured
task/action results are stored separately and are introduced only as untrusted
results during the current turn. When the budget is exceeded, the oldest complete
user turn is removed as a unit. The current turn is never partially truncated; a
current turn that cannot fit fails explicitly. Clearing a conversation deletes
that session and its context metrics. Cancellation prevents late history writes
from restoring cleared turns.

Durable encrypted history and source-linked semantic compaction are implemented.
Compaction begins at 70%, binds summaries structurally to the exact source turn
IDs, retains recent complete turns, and retrieves relevant older summaries with
MiniLM. No summary, history record, or retrieved text can grant action authority.

Dashboard changes support output length, voice and speech speed. They save validated user settings and require an assistant restart. Other settings require editing the user configuration and restarting MacBot. `start` persists the effective configuration so child services agree.

Mute uses AVAudioEngine's voice-processing input mute. The audio device may remain open while playback continues, so macOS may still show its microphone indicator. Browser push-to-talk stops native capture before opening the browser microphone and closes browser tracks after recording. Full device closure occurs on service stop.

## Voice choices

Qwen audition voices are `qwen-aiden-0.6b`, `qwen-ryan-0.6b`,
`qwen-aiden-1.7b`, and `qwen-ryan-1.7b`. Provision their registered model before
selection. Qwen streaming currently uses the model's native speaking rate;
`tts_speed` applies only to Kokoro and Piper. Piper `amy`/`lessac` and Kokoro
Heart/Michael remain explicit fast fallbacks. No voice-quality claim replaces
listening acceptance. Missing models fail explicitly and are disabled in the UI.

After updating from the earlier audio helper, run `macbot build-audio` and restart MacBot. Native IPC protocol 2 uses 16 kHz capture and 48 kHz playback so the voice is no longer downsampled to the STT input rate. The Python bridge rejects an outdated helper with a rebuild instruction. Roll back code, wheel and helper together; voice rollback is selecting a provisioned Piper voice and restarting the assistant.

## Request routing

Direct requests such as “Open Calculator”, “Search the web for sourdough recipes”, “Take a screenshot”, and “What time is it?” select only their relevant tool. Tool output and prior turns cannot select tools for the current turn. Ambiguous follow-ups such as “do that again” require an explicit restatement; compound or unrecognized requests may need to be split. Unsupported file operations remain unavailable. Add `local_time` to `tools.enabled` in an older configuration to enable local clock answers; no network is needed.

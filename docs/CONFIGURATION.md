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
| `tools` | Enabled tool names, allowed applications, screenshot directory, approval lifetime (60 seconds). |

Existing Qwen3-4B remains the default until full candidate acceptance. Change `models.llm` explicitly to compare `lfm-1.2b` or `qwen3.5-2b`. MLX names have `-mlx` suffix and require `llm_backend: mlx` and the `mlx` extra. Selecting a missing backend/model fails; no substitution or runtime downloads occur.

Dashboard changes support output length, voice and speech speed. They save validated user settings and require an assistant restart. Other settings require editing the user configuration and restarting MacBot. `start` persists the effective configuration so child services agree.

Mute uses AVAudioEngine's voice-processing input mute. The audio device may remain open while playback continues, so macOS may still show its microphone indicator. Browser push-to-talk stops native capture before opening the browser microphone and closes browser tracks after recording. Full device closure occurs on service stop.

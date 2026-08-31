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
| `models` | Registered LLM name, `llm_backend` (`llama` or `mlx`), local LLM URL, context length, maximum output tokens, temperature, threads, STT (`parakeet` or `whisper`), and registered voice. |
| `audio` | Endpoint silence 350 ms, pre-roll 256 ms, speech start 96 ms, maximum utterance 30 seconds, idle capture timeout 300 seconds, VAD threshold 0.5. |
| `tools` | Enabled capability names, allowed applications, and screenshot directory. Conversation cannot execute side effects; explicit Task plans require bounded native authorization. |
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
its messages and summaries while retaining its durable Task ledger. Cancellation prevents late history writes
from restoring cleared turns.

Durable encrypted history and source-linked semantic compaction are implemented.
Compaction begins at 70%, binds summaries structurally to the exact source turn
IDs, retains recent complete turns, and retrieves relevant older summaries with
MiniLM. No summary, history record, or retrieved text can grant action authority.

The native Settings view supports retention, endpoint timing, context target,
installed voice selection, and the Brave credential. It distinguishes edited,
saved, and active values and offers a controlled local-service restart after a
validated save. The browser diagnostic view is read-only. Other settings require
editing the user configuration and restarting MacBot. `start` persists the
effective configuration so child services agree.

Mute uses AVAudioEngine's voice-processing input mute. The audio device may remain open while playback continues, so macOS may still show its microphone indicator. Full device closure occurs on service stop.

## Voice choices

The accepted product voice is `qwen-aiden-1.7b`, using its native speaking rate.
Speech-speed control is not exposed because this implementation does not honor
it. Other registered voices belong to explicit audition and acceptance tooling;
none is an implicit substitute. Missing models fail explicitly. No automated
voice check replaces listening acceptance.

After updating from the earlier audio helper, run `macbot build-audio` and restart MacBot. Native IPC protocol 2 uses 16 kHz capture and 48 kHz playback so the voice is no longer downsampled to the STT input rate. The Python bridge rejects an outdated helper with a rebuild instruction. Roll back code, wheel and helper together; voice rollback is selecting a provisioned Piper voice and restarting the assistant.

## Request routing

Conversation can deterministically select read-only local time, system status,
document retrieval, configured search, and weather enrichment from the current
message. It cannot execute side effects. App opening, exact-URL opening, and
screenshots require explicit Task mode and remain outside the first research
Task release gate. Tool output and prior turns cannot select capabilities or
extend authority. Ambiguous follow-ups require an explicit restatement.

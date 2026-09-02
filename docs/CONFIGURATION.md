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
| `models` | Registered LLM name, `llm_backend` (`llama` or `mlx` for lab comparisons), local LLM URL, context length, maximum output tokens, temperature, threads, the sole `parakeet` STT, registered Qwen voice, and MiniLM embedding role. |
| `audio` | Endpoint silence 350 ms, pre-roll 256 ms, speech start 96 ms, maximum utterance 30 seconds, idle capture timeout 300 seconds, VAD threshold 0.5. |
| `tools` | Exactly `rag_search`, `web_search`, and `web_fetch` for the research release. Conversation cannot execute side effects; explicit Task plans require bounded native authorization. |
| `privacy` | Encrypted history enablement and retention in days (default 30). The encryption key is held in Keychain. |

The typed production manifest selects exactly one artifact per role:
`qwen3.5-2b-official`, Parakeet, Qwen3-TTS 1.7B Aiden, MiniLM, and Silero VAD.
Alternative catalog entries are lab inputs, not runtime fallback choices.
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
Task observations and evidence are stored separately and enter prompts through
a dedicated untrusted evidence envelope. The complete prompt budget includes
system instructions, active turns, recalled summaries, evidence, and the
generation allowance. When the budget is exceeded, the oldest complete user
turn is removed as a unit. The current turn is never partially truncated; a
current turn that cannot fit fails explicitly. Clearing a conversation deletes
its messages and summaries while retaining its durable Task ledger. Generation
ownership prevents a stale turn from restoring cleared or compacted history.

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

## Voice and audio

The release configuration selects `qwen-aiden-1.7b` at its native speaking rate.
Speech-speed control is not exposed because this backend does not honor it.
Missing models fail explicitly. No automated voice check replaces listening
acceptance.

Swift owns released capture and playback. It sends 16 kHz microphone PCM and
plays TTS PCM at the model-reported rate over authenticated native audio IPC.
There is no Python audio-helper build, helper protocol, Whisper recognizer, or
voice fallback to provision or roll back.

## Request routing

Conversation can deterministically select read-only local time, system status,
document retrieval, configured search, and weather enrichment from the current
message. It cannot execute side effects. Durable research Tasks can use only
`rag_search`, `web_search`, and bounded `web_fetch`. App control, URL opening,
screenshots, shell, arbitrary writes, scheduling, messaging, MCP, and delegation
are outside this release. Tool output and prior turns cannot select capabilities
or extend authority. Ambiguous follow-ups require an explicit restatement.

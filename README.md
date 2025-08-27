# Local Voice Assistant (macOS, fully offline)

This starter kit wires up a **5-model local pipeline** on Apple Silicon:

**VAD + Turn Detection → Whisper.cpp (STT) → llama.cpp (LLM) → Kokoro (TTS)**

Everything runs **offline** on your Mac (M-series strongly recommended).

---

## Quick Start

### 0) Prereqs (first time only)
```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install cmake ffmpeg portaudio python@3.11 git
```

### 1) Create venv + Python deps
```bash
make venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Build Whisper.cpp (Core ML on Apple ANE)
```bash
make build-whisper
# test
./whisper.cpp/build/bin/whisper-cli -m whisper.cpp/models/ggml-base.en.bin -f whisper.cpp/samples/jfk.wav
```

### 3) Build llama.cpp (Metal) + run server
1. Download a **GGUF** instruct model (one file) into `llama.cpp/models/` (examples: Llama-3.1-8B-Instruct Q4_K_M, Mistral-7B-Instruct Q4_K_M).
2. Start the server:
```bash
make run-llama
```
The server exposes OpenAI-compatible routes on **http://localhost:8080**.

### 4) Configure (optional)
Edit `config.yaml` to change any paths, model, voice, or system prompt.

### 5) Run the assistant
```bash
source .venv/bin/activate
make run-assistant
```
Speak after the tiny beep. Press **Ctrl+C** to stop.

---

## Notes

- **Model files are not included.** Place your chosen `.gguf` in `llama.cpp/models/` and Whisper model `.bin` is auto-downloaded (`base.en` by default).
- If the assistant stutters under load, lower llama server context (`LLAMA_CTX` in `Makefile`) or pick a smaller quant (Q4_K_M).
- Grant Microphone permissions to your Terminal (System Settings → Privacy & Security → Microphone).

---

## Common Tweaks

- **Lower latency LLM:** reduce `MAX_TOKENS` and context in `Makefile` vars.
- **Whisper speed:** smaller Whisper model (`tiny.en` or `base.en`) is snappier.
- **Barge-in (advanced):** enable interrupt-on-voice in `voice_assistant.py` by watching mic during TTS and stopping playback when voice is detected.

---

## Project Layout

```
MacBot/
├─ README.md
├─ Makefile
├─ config.yaml
├─ requirements.txt
├─ voice_assistant.py
├─ scripts/
│  └─ bootstrap_mac.sh
├─ .gitignore
├─ whisper.cpp/         # cloned & built by Makefile
└─ llama.cpp/           # cloned & built by Makefile
```

---

## License

This starter code is MIT. Check respective upstream repos for their licenses and model licenses.

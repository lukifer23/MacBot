#!/usr/bin/env bash
set -euo pipefail

echo "Installing Xcode Command Line Tools (if needed)..."
xcode-select --install || true

if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

echo "Installing packages via Homebrew..."
brew install cmake ffmpeg portaudio python@3.11 git

echo "Done. Next:"
echo "  1) make venv && source .venv/bin/activate && pip install -r requirements.txt"
echo "  2) make build-whisper"
echo "  3) make build-llama"
echo "  4) Place a GGUF model in llama.cpp/models, then: make run-llama"
echo "  5) make run-assistant"

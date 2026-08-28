#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'MacBot native setup requires Apple Silicon macOS.' >&2
  exit 1
fi
for tool in uv git cmake ffmpeg xcrun; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing prerequisite: $tool. Install it before running setup." >&2
    exit 1
  fi
done
xcrun --find swiftc >/dev/null
uv sync --frozen --all-extras --group dev
uv run --frozen macbot setup
echo 'Environment ready. Run make build and make models explicitly to provision native engines and model weights.'

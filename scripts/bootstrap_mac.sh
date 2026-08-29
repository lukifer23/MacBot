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
# File Provider can mark newly-created .pth files hidden in synchronized
# folders. Python 3.12 deliberately skips those files, which breaks editable
# imports in child processes even though the parent pytest process has a source
# path. Clear only that metadata flag; installed-wheel runtimes do not use an
# editable .pth file.
find .venv/lib/python3.12/site-packages -maxdepth 1 -name '*.pth' -exec chflags nohidden {} +
PYTHONPATH="$PWD/src" .venv/bin/macbot setup
echo 'Environment ready. Run make build and make models explicitly to provision native engines and model weights.'

"""Test process environment shared by real subprocess integration tests."""

from __future__ import annotations

import os
from pathlib import Path

# macOS File Provider can apply UF_HIDDEN to editable-install .pth files inside
# synchronized folders. Python skips hidden .pth files, but child processes in
# source-checkout integration tests still need the same source tree as pytest.
SOURCE = str(Path(__file__).resolve().parents[1] / "src")
existing = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = SOURCE + (os.pathsep + existing if existing else "")

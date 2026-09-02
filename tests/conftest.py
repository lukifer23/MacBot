"""Test process environment shared by real subprocess integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from macbot.config import Settings
from macbot.residency import InferenceResidencyLease

# macOS File Provider can apply UF_HIDDEN to editable-install .pth files inside
# synchronized folders. Python skips hidden .pth files, but child processes in
# source-checkout integration tests still need the same source tree as pytest.
SOURCE = str(Path(__file__).resolve().parents[1] / "src")
existing = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = SOURCE + (os.pathsep + existing if existing else "")


@pytest.fixture(scope="session", autouse=True)
def exclusive_selected_model_residency(request):
    """Never run real selected-model tests beside a production model stack."""
    if not any(item.get_closest_marker("models") for item in request.session.items):
        yield
        return
    lease = InferenceResidencyLease(Settings().data_dir, purpose="selected-model-tests")
    lease.acquire()
    try:
        yield
    finally:
        lease.release()

import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from macbot import voice_assistant as va


def test_transcribe_cli_reads_cli_output(monkeypatch):
    transcript_text = "mock transcript"
    created_paths = []

    def fake_run(cmd, capture_output, text, timeout):
        base_index = cmd.index("-of") + 1
        base_path = cmd[base_index]
        output_path = f"{base_path}.txt"
        created_paths.append(output_path)
        with open(output_path, "w") as handle:
            handle.write(transcript_text)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(va.subprocess, "run", fake_run)
    monkeypatch.setattr(va, "_WHISPER_IMPL", "cli")
    monkeypatch.setattr(va, "_WHISPER_CTX", None)
    monkeypatch.setattr(va, "sf", None)

    audio = np.zeros(1600, dtype=np.float32)
    result = va.transcribe(audio)

    assert result == transcript_text
    for path in created_paths:
        assert not os.path.exists(path)

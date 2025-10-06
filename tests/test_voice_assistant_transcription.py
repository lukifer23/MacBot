import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from macbot import voice_assistant as va


def _make_chunk(duration_sec, sample_rate=16000, amplitude=0.1):
    samples = int(duration_sec * sample_rate)
    return np.full(samples, amplitude, dtype=np.float32)


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


def test_streaming_transcriber_avoids_reprocessing(monkeypatch):
    call_sizes = []
    outputs = ["hello", "world"]

    def fake_transcribe(audio):
        call_sizes.append(len(audio))
        return outputs[len(call_sizes) - 1]

    monkeypatch.setattr(va, "transcribe", fake_transcribe)

    transcriber = va.StreamingTranscriber(max_buffer_duration=5.0, sample_rate=16000)
    transcriber._transcription_interval = 0.0

    assert transcriber.add_chunk(_make_chunk(0.2)) == ""
    assert call_sizes == []

    delta = transcriber.add_chunk(_make_chunk(0.4))
    assert delta == "hello"
    assert call_sizes == [9600]

    delta = transcriber.add_chunk(_make_chunk(0.5))
    assert delta == "world"
    assert call_sizes == [9600, 8000]

    assert len(transcriber._segments) == 2
    assert transcriber._segments[0]["start"] == 0
    assert transcriber._segments[0]["end"] == 9600
    assert transcriber._segments[1]["start"] == 9600
    assert transcriber._segments[1]["end"] == 17600

    assert transcriber.flush() == "hello world"

"""Real resident recognition of a pinned human recording, not generated noise."""

import hashlib
import json
import time

import pytest
import soundfile as sf

from macbot.config import load
from macbot.runtime import Runtime
from macbot.speech import Transcriber

pytestmark = pytest.mark.models


@pytest.mark.parametrize("backend", ["parakeet", "whisper"])
def test_resident_transcriber_recognizes_complete_human_recording(backend):
    s = load()
    s.models.stt = backend
    root = s.data_dir / "benchmarks/librispeech"
    assert (root / "manifest.json").is_file(), "Run scripts/provision_benchmark_audio.py explicitly"
    record = json.loads((root / "manifest.json").read_text())["records"][0]
    path = root / record["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
    samples, sr = sf.read(path, dtype="float32")
    assert sr == 16000
    model = Transcriber(s)
    try:
        first = model.transcribe(samples)
        second = model.transcribe(samples)
        for transcript in [first, second]:
            assert "quilter" in transcript.lower()
            assert "gospel" in transcript.lower(), "Final audio/transcription tail was lost"
    finally:
        model.close()


def test_real_capture_pipeline_publishes_interim_transcription():
    settings = load()
    settings.privacy.history_enabled = False
    settings.models.stt = "parakeet"
    settings.models.tts_voice = "lessac"
    settings.audio.endpoint_ms = 2000
    root = settings.data_dir / "benchmarks/librispeech"
    record = json.loads((root / "manifest.json").read_text())["records"][0]
    samples, rate = sf.read(root / record["file"], dtype="float32")
    assert rate == 16000

    runtime = Runtime(settings)
    try:
        runtime.listening = True
        runtime.capture_session = "partial-test"
        for offset in range(0, len(samples), 512):
            frame = samples[offset : offset + 512]
            if len(frame) < 512:
                break
            runtime.audio.capture.put(frame.copy(), timeout=1)
        deadline = time.monotonic() + 10
        partial = None
        cursor = 0
        while time.monotonic() < deadline and partial is None:
            journal = runtime.events.read(cursor, timeout=0.25)
            cursor = journal["cursor"]
            partial = next(
                (
                    event
                    for event in journal["events"]
                    if event["kind"] == "transcription" and event["data"].get("partial")
                ),
                None,
            )
        assert partial is not None
        assert partial["session_id"] == "partial-test"
        assert partial["turn_id"]
        assert partial["data"]["text"].strip()
    finally:
        runtime.listening = False
        runtime.capture_epoch += 1
        runtime.close()

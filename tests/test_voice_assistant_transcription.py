"""Real resident recognition of a pinned human recording, not generated noise."""

import hashlib
import json
import time

import numpy as np
import pytest
import soundfile as sf

from macbot.config import Settings
from macbot.runtime import Runtime
from macbot.speech import Transcriber

pytestmark = pytest.mark.models


def test_resident_transcriber_recognizes_complete_human_recording():
    s = Settings()
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


def test_real_capture_pipeline_reports_vad_activity_and_only_final_transcription():
    settings = Settings()
    settings.privacy.history_enabled = False
    settings.models.stt = "parakeet"
    settings.audio.endpoint_ms = 350
    root = settings.data_dir / "benchmarks/librispeech"
    record = json.loads((root / "manifest.json").read_text())["records"][0]
    samples, rate = sf.read(root / record["file"], dtype="float32")
    assert rate == 16000

    runtime = Runtime(settings, load_tts=False)
    try:
        runtime.listening = True
        runtime.capture_session = "partial-test"
        for offset in range(0, len(samples), 512):
            frame = samples[offset : offset + 512]
            if len(frame) < 512:
                break
            runtime.audio.capture.put(frame.copy(), timeout=1)
        for _ in range(20):
            runtime.audio.capture.put(np.zeros(512, dtype="float32"), timeout=1)
        deadline = time.monotonic() + 10
        activity = None
        final = None
        cursor = 0
        seen_partial = False
        while time.monotonic() < deadline and final is None:
            journal = runtime.events.read(cursor, timeout=0.25)
            cursor = journal["cursor"]
            activity = activity or next(
                (
                    event
                    for event in journal["events"]
                    if event["kind"] == "capture_activity" and event["data"].get("active")
                ),
                None,
            )
            seen_partial = seen_partial or any(
                event["kind"] == "transcription" and event["data"].get("partial")
                for event in journal["events"]
            )
            final = next(
                (
                    event
                    for event in journal["events"]
                    if event["kind"] == "transcription" and not event["data"].get("partial")
                ),
                final,
            )
        assert activity is not None
        assert final is not None
        assert final["session_id"] == "partial-test"
        assert final["data"]["text"].strip()
        assert not seen_partial
    finally:
        runtime.listening = False
        runtime.capture_epoch += 1
        runtime.close()

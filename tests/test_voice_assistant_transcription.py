"""Real resident recognition of a pinned human recording, not generated noise."""

import hashlib
import json
import time

import numpy as np
import pytest
import soundfile as sf

from macbot.config import Settings, load, prepare
from macbot.runtime import Runtime
from macbot.speech import Transcriber

pytestmark = pytest.mark.models


def test_whistle_resident_preview_and_dictation():
    settings = load()
    settings.models.profile = "whistle"
    settings.models.stt = "whistle"
    settings.privacy.history_enabled = False
    prepare(settings)
    root = settings.data_dir / "benchmarks/librispeech"
    record = json.loads((root / "manifest.json").read_text())["records"][0]
    path = root / record["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
    samples, rate = sf.read(path, dtype="float32")
    assert rate == 16000 and samples.ndim == 1

    runtime = Runtime(settings, load_tts=False)
    try:
        model = runtime.transcriber
        assert model is not None
        assert model.transcribe(np.zeros(16000, dtype=np.float32)) == ""
        previews = list(model.preview(samples[i : i + 6400] for i in range(0, len(samples), 6400)))
        assert any(text for text in previews)
        assert "gospel" in model.transcribe(samples).lower()

        runtime.capture_mode = "dictation"
        runtime.capture_session = "whistle-validation"
        runtime.listening = True
        # Supply the same 32 ms PCM frames that the native capture path queues.
        trailing_silence = np.zeros(20 * 512, dtype=np.float32)
        capture = np.concatenate((samples, trailing_silence))
        for offset in range(0, len(capture) - 511, 512):
            runtime.audio.capture.put(
                (runtime.capture_epoch, capture[offset : offset + 512].copy()), timeout=1
            )
        cursor = 0
        heard_speech = False
        transcript = []
        until = time.monotonic() + 10
        while time.monotonic() < until:
            batch = runtime.events.read(cursor, timeout=0.25)
            cursor = batch["cursor"]
            for event in batch["events"]:
                if event["session_id"] != "whistle-validation":
                    continue
                assert event["state"] != "failed", event
                if event["kind"] == "capture_activity" and event["data"].get("active"):
                    heard_speech = True
                if event["kind"] == "dictation":
                    assert heard_speech
                    transcript.append(event["data"]["text"])
                    if "gospel" in " ".join(transcript).lower():
                        return
        pytest.fail("Whistle dictation did not emit its final transcript")
    finally:
        runtime.close()


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
            runtime.audio.capture.put((runtime.capture_epoch, frame.copy()), timeout=1)
        for _ in range(20):
            runtime.audio.capture.put(
                (runtime.capture_epoch, np.zeros(512, dtype="float32")), timeout=1
            )
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

"""Real resident recognition of a pinned human recording, not generated noise."""

import hashlib
import json

import pytest
import soundfile as sf

from macbot.config import load
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

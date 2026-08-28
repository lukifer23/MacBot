"""Physical device integration; never converts unavailable hardware into a passing skip.

Run only with an operator present: MACBOT_DEVICE_TEST=1 uv run --frozen pytest -m device.
The explicit flag authorizes this test to open the microphone and play a short sentence.
This test does not replace acoustic latency measurement or user listening acceptance.
"""

import os
import threading
import time

import numpy as np
import pytest

from macbot.config import load
from macbot.native_audio import NativeAudio
from macbot.speech import Synthesizer

pytestmark = pytest.mark.device


@pytest.mark.parametrize("start_muted", [False, True])
@pytest.mark.parametrize("voice", ["lessac", "kokoro-heart"])
def test_shared_engine_capture_playback_and_cancel_acknowledgement(start_muted, voice):
    assert os.environ.get("MACBOT_DEVICE_TEST") == "1", (
        "Device gate unrun: operator must explicitly authorize microphone and speaker test with MACBOT_DEVICE_TEST=1"
    )
    settings = load()
    settings.models.tts_voice = voice
    events = []
    audio = NativeAudio(settings, events.append)
    synth = Synthesizer(settings)
    cancel = threading.Event()
    errors = []

    def playback():
        try:
            for samples, rate in synth.chunks(
                "MacBot audio check. Please speak while this sentence is playing.", cancel
            ):
                audio.play(samples, rate, cancel, audio.generation)
        except Exception as exc:
            errors.append(exc)

    thread = None
    try:
        audio.launch(capture=not start_muted)
        assert audio.ready and audio.aec
        ready = next(e for e in events if e["event"] == "ready")
        assert ready["input_sample_rate"] > 0
        assert ready["input_sample_rate"] == ready["output_sample_rate"]
        assert ready["sample_rate"] == 16000
        assert ready["playback_sample_rate"] == 48000
        assert ready["protocol"] == 2
        if start_muted:
            audio.launch(capture=True)
        thread = threading.Thread(target=playback)
        thread.start()
        deadline = time.monotonic() + 10
        while (
            not any(e["event"] == "playback_scheduled" for e in events)
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert any(e["event"] == "playback_scheduled" for e in events), errors
        # A live pipe can still contain only zeros if Core Audio's discrete
        # channel layout is implicitly mapped to an unconnected mono channel.
        # This device gate requires actual input, not merely allocated frames.
        peak = 0.0
        deadline = time.monotonic() + 3
        while peak == 0.0 and time.monotonic() < deadline:
            frame = audio.capture.get(timeout=1)
            assert frame.size > 0 and np.isfinite(frame).all()
            peak = float(np.max(np.abs(frame)))
        assert peak > 0.0, "Microphone frames are all silent; capture-to-mono path is broken"
        signal = audio.input_status()
        assert signal["receiving"] and signal["frames"] > 0
        assert signal["peak"] > 0 and signal["rms"] > 0
        requested = time.monotonic_ns()
        cancel.set()
        generation = audio.cancel()
        deadline = time.monotonic() + 1
        while (
            not any(e["event"] == "stopped" and e.get("generation") == generation for e in events)
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        stopped = [
            e for e in events if e["event"] == "stopped" and e.get("generation") == generation
        ]
        assert stopped
        # IPC/player-stop acknowledgement only, not the acoustic end-to-end gate.
        assert (time.monotonic_ns() - requested) / 1e6 < 250
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert not errors
    finally:
        cancel.set()
        audio.close()
        if thread:
            thread.join(timeout=3)

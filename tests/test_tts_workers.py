import os
import sys
import threading
import time

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot import voice_assistant


@pytest.mark.parametrize("job_count", [voice_assistant.MAX_CONCURRENT_TTS + 2])
def test_tts_concurrency_respects_limit(monkeypatch, job_count):
    active_counts = []
    release = threading.Event()
    start_gate = threading.Event()

    def fake_process(self, text, interruptible, notify):
        with self._tts_count_lock:
            active_now = self._active_tts_count
        active_counts.append(active_now)
        if len(active_counts) >= voice_assistant.MAX_CONCURRENT_TTS:
            start_gate.set()
        assert release.wait(timeout=1.5)
        return True

    monkeypatch.setattr(
        voice_assistant.TTSManager,
        "_process_speak_request",
        fake_process,
        raising=False,
    )

    manager = voice_assistant.TTSManager()
    try:
        jobs = [
            manager.enqueue_speak(f"chunk {idx}", interruptible=True, notify=False)
            for idx in range(job_count)
        ]

        assert start_gate.wait(timeout=1.5)

        with manager._tts_count_lock:
            assert manager._active_tts_count == voice_assistant.MAX_CONCURRENT_TTS

        release.set()

        for job in jobs:
            assert job.wait(timeout=1.5)

        assert active_counts
        assert max(active_counts) <= voice_assistant.MAX_CONCURRENT_TTS
    finally:
        release.set()
        manager.cleanup()


def test_tts_cleanup_blocks_until_workers_finish(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_process(self, text, interruptible, notify):
        started.set()
        assert release.wait(timeout=1.5)
        return True

    monkeypatch.setattr(
        voice_assistant.TTSManager,
        "_process_speak_request",
        blocking_process,
        raising=False,
    )

    manager = voice_assistant.TTSManager()
    job = manager.enqueue_speak("blocking", interruptible=False, notify=False)

    try:
        assert started.wait(timeout=1.5)

        cleanup_thread = threading.Thread(target=manager.cleanup)
        cleanup_thread.start()
        time.sleep(0.2)
        assert cleanup_thread.is_alive()

        release.set()
        cleanup_thread.join(timeout=2.0)
        assert not cleanup_thread.is_alive()
        assert job.wait(timeout=1.5)
    finally:
        release.set()
        if manager._tts_workers:
            manager.cleanup()

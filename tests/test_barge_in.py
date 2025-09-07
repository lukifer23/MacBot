import threading
import time
import numpy as np
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.conversation_manager import ConversationManager, ConversationState

SAMPLE_RATE = 24000


class DummyAudioHandler:
    """Simulates AudioInterruptHandler for testing."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.interrupt_requested = False

    def play_audio(self, audio_data, on_interrupt=None):
        duration = len(audio_data) / self.sample_rate
        start = time.time()
        while time.time() - start < duration:
            if self.interrupt_requested:
                if on_interrupt:
                    on_interrupt()
                return False
            time.sleep(0.01)
        return True

    def interrupt_playback(self):
        self.interrupt_requested = True


def async_speak(handler, cm, text):
    t = np.linspace(0, 3.0, int(SAMPLE_RATE * 3.0), False)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    chunk = int(SAMPLE_RATE * 0.25)
    total = len(audio)
    idx = 0
    cm.start_response(text)
    while idx < total:
        if cm.current_context.current_state == ConversationState.INTERRUPTED:
            remaining_index = int(len(text) * idx / total)
            cm.current_context.buffered_response = text[remaining_index:]
            cm.current_context.ai_response = text[:remaining_index]
            return
        piece = audio[idx : idx + chunk]
        handler.play_audio(piece)
        idx += len(piece)
        spoken = int(len(text) * idx / total)
        cm.update_response(text[:spoken])
    cm.update_response(text)
    cm.complete_response()


def test_barge_in_during_long_response():
    handler = DummyAudioHandler(SAMPLE_RATE)
    cm = ConversationManager()
    cm.start_conversation()
    long_text = "hello " * 100

    def interrupter():
        time.sleep(0.2)
        cm.interrupt_response()
        handler.interrupt_playback()

    threading.Thread(target=interrupter).start()
    async_speak(handler, cm, long_text)

    assert cm.current_context.current_state == ConversationState.INTERRUPTED

    remaining = cm.resume_response()
    assert remaining

    async_speak(handler, cm, remaining)

    assert cm.current_context.current_state == ConversationState.IDLE
    assert cm.current_context.ai_response == long_text

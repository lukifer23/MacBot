import os
import sys
import time
import threading
import numpy as np
import pytest
import types

# Ensure the package can be imported without installation
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

# Provide stub modules for optional audio dependencies
sys.modules.setdefault('sounddevice', types.SimpleNamespace(OutputStream=object))
sys.modules.setdefault('soundfile', types.SimpleNamespace())

from macbot.audio_interrupt import AudioInterruptHandler
from macbot.conversation_manager import ConversationManager, ConversationState, ResponseState



def test_audio_interruption(monkeypatch):
    """Audio playback can be interrupted."""
    handler = AudioInterruptHandler(sample_rate=24000)

    def fake_worker(self):
        while self.is_playing and not self.interrupt_requested:
            time.sleep(0.01)
        self.is_playing = False

    monkeypatch.setattr(AudioInterruptHandler, '_playback_worker', fake_worker)
    audio = np.zeros(2400, dtype=np.float32)
    result = {}

    def run_play():
        result['completed'] = handler.play_audio(audio)

    t = threading.Thread(target=run_play)
    t.start()
    time.sleep(0.05)
    handler.interrupt_playback()
    t.join()

    assert result['completed'] is False
    status = handler.get_playback_status()
    assert not status['is_playing']
    assert status['interrupt_requested']


def test_conversation_state_transitions():
    """Conversation manager handles state transitions and buffering."""
    manager = ConversationManager()
    manager.lock = threading.RLock()
    conv_id = manager.start_conversation()
    assert manager.current_context.conversation_id == conv_id

    manager.add_user_input('Hello')
    manager.start_response('Hi there')
    assert manager.current_context.current_state == ConversationState.SPEAKING

    manager.interrupt_response()
    assert manager.current_context.current_state == ConversationState.INTERRUPTED

    buffered = manager.resume_response()
    assert buffered == 'Hi there'

    manager.complete_response()
    assert manager.current_context.current_state == ConversationState.IDLE
    assert len(manager.get_recent_history()) == 2

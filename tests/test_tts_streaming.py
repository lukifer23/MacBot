import os
import sys
import pytest

# Ensure package import without installation
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.conversation_manager import ConversationManager, ResponseState
import threading


def test_streaming_response_updates_history():
    manager = ConversationManager()
    manager.lock = threading.RLock()
    manager.start_conversation()
    manager.start_response()

    manager.update_response('Hello', is_complete=False)
    assert manager.current_context.ai_response == 'Hello'
    assert manager.current_context.response_state == ResponseState.STREAMING

    manager.update_response('Hello world', is_complete=True)
    assert manager.current_context.ai_response == 'Hello world'
    assert manager.current_context.response_state == ResponseState.COMPLETED

    history = manager.get_recent_history()
    assert history[-1]['content'] == 'Hello world'

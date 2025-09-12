import os
import sys
import threading

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.conversation_manager import ConversationManager, ConversationState


def test_state_callback_no_deadlock():
    manager = ConversationManager()
    call_count = 0

    def callback(ctx):
        nonlocal call_count
        call_count += 1
        # Interact with manager inside callback
        manager.get_conversation_summary()
        if ctx.current_state != ConversationState.ERROR:
            manager.update_state(ConversationState.ERROR)

    manager.register_state_callback(callback)

    thread = threading.Thread(target=manager.start_conversation, args=("test",))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "Deadlock occurred in start_conversation"

    summary = manager.get_conversation_summary()
    assert summary["current_state"] == ConversationState.ERROR.value
    assert call_count == 2

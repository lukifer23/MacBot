import threading
import os
import sys

# Ensure the source directory is on the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.conversation_manager import ConversationManager, ConversationState


def run_with_timeout(func, timeout=1.0):
    """Run `func` in a thread and ensure it completes within timeout."""
    thread = threading.Thread(target=func)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "Operation deadlocked"


def test_interrupt_resume_no_deadlock():
    """Interrupting and resuming should not deadlock the manager."""
    cm = ConversationManager()
    cm.start_conversation()
    cm.start_response("hello")

    # Interrupt should complete without hanging
    run_with_timeout(cm.interrupt_response)
    assert cm.current_context.current_state == ConversationState.INTERRUPTED

    # Resume should also complete without hanging
    run_with_timeout(lambda: cm.resume_response())
    assert cm.current_context.current_state == ConversationState.SPEAKING


def test_clear_conversation_no_deadlock():
    """Clearing the conversation should not deadlock."""
    cm = ConversationManager()
    cm.start_conversation()
    cm.start_response("hi")
    cm.interrupt_response()

    run_with_timeout(cm.clear_conversation)
    assert cm.current_context is None

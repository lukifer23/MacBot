import os
import sys
import types

sys.modules.setdefault(
    "pyttsx3",
    types.SimpleNamespace(
        init=lambda: types.SimpleNamespace(
            say=lambda *a, **k: None,
            runAndWait=lambda *a, **k: None,
            connect=lambda *a, **k: None,
            stop=lambda *a, **k: None,
            setProperty=lambda *a, **k: None,
        )
    ),
)

os.environ["MACBOT_TEST_MODE"] = "1"
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

import macbot.config as CFG
CFG.interruption_enabled = lambda: False

from macbot.conversation_manager import ConversationManager, ResponseState
from macbot.voice_assistant import build_chat_messages


def _simulate_turn(manager: ConversationManager, user_text: str, ai_text: str, complete: bool = True):
    manager.add_user_input(user_text)
    manager.start_response()
    manager.update_response(ai_text, is_complete=complete)


def test_multi_turn_context_retained():
    manager = ConversationManager()
    manager.start_conversation("conv1")
    _simulate_turn(manager, "Hello", "Hi there!")
    _simulate_turn(manager, "How are you?", "I'm good.")
    messages = build_chat_messages("Tell me a joke", manager)
    roles = [m["role"] for m in messages]
    contents = [m["content"] for m in messages]
    assert roles == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert contents[1] == "Hello"
    assert contents[2] == "Hi there!"
    assert contents[3] == "How are you?"
    assert contents[4] == "I'm good."
    assert contents[5] == "Tell me a joke"


def test_interrupted_response_included():
    manager = ConversationManager()
    manager.start_conversation("conv2")
    manager.add_user_input("Hello")
    manager.start_response()
    manager.update_response("Hi", is_complete=False)
    manager.interrupt_response()
    assert manager.current_context.response_state == ResponseState.INTERRUPTED
    messages = build_chat_messages("Continue", manager)
    assert messages[-2]["role"] == "assistant"
    assert messages[-2]["content"] == "Hi"
    assert messages[-1] == {"role": "user", "content": "Continue"}

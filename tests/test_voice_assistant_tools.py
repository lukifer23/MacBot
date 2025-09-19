import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot import voice_assistant as va


class _DummyResponse:
    def __init__(self):
        self.status_code = 200
        self._lines = [
            'data: {"choices": [{"delta": {"content": "Hi"}}]}',
            'data: [DONE]'
        ]

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _stub_environment(monkeypatch):
    monkeypatch.setattr(va, "_notify_dashboard_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(va, "INTERRUPTION_ENABLED", False)
    dummy_audio = type("DummyAudio", (), {"interrupt_requested": False, "check_voice_activity": lambda *args, **kwargs: False})()
    monkeypatch.setattr(va.tts_manager, "audio_handler", dummy_audio, raising=False)
    monkeypatch.setattr(va.tts_manager, "speak", lambda *args, **kwargs: None)


@pytest.fixture
def llm_post(monkeypatch):
    post_mock = MagicMock(side_effect=lambda *args, **kwargs: _DummyResponse())
    monkeypatch.setattr(va.requests, "post", post_mock)
    return post_mock


TOOL_CASES = [
    ("web_search", "search the web for cats", "web_search"),
    ("app_launcher", "open app safari", "open_app"),
    ("screenshot", "please take a screenshot", "take_screenshot"),
    ("weather", "what's the weather today", "get_weather"),
    ("system_monitor", "show system info", "get_system_info"),
    ("rag_search", "search knowledge base for docs", "search_knowledge_base"),
]


@pytest.mark.parametrize("tool_name,user_text,method_name", TOOL_CASES)
def test_llama_chat_calls_enabled_tools(monkeypatch, llm_post, tool_name, user_text, method_name):
    monkeypatch.setattr(va.CFG, "get_enabled_tools", lambda: [tool_name])
    monkeypatch.setattr(va, "tool_caller", va.ToolCaller())

    tool_mock = MagicMock(return_value="TOOL RESPONSE")
    monkeypatch.setattr(va.tool_caller, method_name, tool_mock)

    va.llama_chat(user_text)

    assert tool_mock.call_count == 1
    assert llm_post.call_count == 0


@pytest.mark.parametrize("tool_name,user_text,method_name", TOOL_CASES)
def test_llama_chat_skips_disabled_tools(monkeypatch, llm_post, tool_name, user_text, method_name):
    monkeypatch.setattr(va.CFG, "get_enabled_tools", lambda: [])
    monkeypatch.setattr(va, "tool_caller", va.ToolCaller())

    tool_mock = MagicMock(return_value="TOOL RESPONSE")
    monkeypatch.setattr(va.tool_caller, method_name, tool_mock)

    va.llama_chat(user_text)

    assert tool_mock.call_count == 0
    # Should fall back to LLM streaming when tools are disabled
    assert llm_post.call_count == 1

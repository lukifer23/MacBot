import os
import sys
import pytest
import types

# Ensure package import without installation
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

# Provide stubs for optional dependencies used by voice_assistant
sys.modules.setdefault('sounddevice', types.SimpleNamespace())
sys.modules.setdefault('soundfile', types.SimpleNamespace())
sys.modules.setdefault('psutil', types.SimpleNamespace())
sys.modules.setdefault('requests', types.SimpleNamespace(get=lambda *a, **k: None, post=lambda *a, **k: None))

from macbot.voice_assistant import ToolCaller, tools


def test_tool_caller_web_search(monkeypatch):
    caller = ToolCaller()
    called = {}

    def fake_search(query):
        called['query'] = query
        return 'ok'

    monkeypatch.setattr(tools, 'web_search', fake_search)
    result = caller.web_search('python')
    assert result == 'ok'
    assert called['query'] == 'python'


def test_tool_caller_error_handling(monkeypatch):
    caller = ToolCaller()

    def boom(query):
        raise RuntimeError('fail')

    monkeypatch.setattr(tools, 'web_search', boom)
    result = caller.web_search('python')
    assert "couldn't perform" in result.lower()

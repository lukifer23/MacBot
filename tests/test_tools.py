import time

import pytest

from macbot.auth import AuthStore
from macbot.config import Settings, prepare
from macbot.model_tool_parser import parse_calls
from macbot.tools import Tools


@pytest.fixture
def registry(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    yield tools
    tools.close()
    auth.close()


def test_read_only_system_metrics_are_real_and_prompt(registry):
    start = time.monotonic()
    result = registry.read("system_info", {})
    assert set(result) == {"cpu_percent", "memory_percent", "disk_percent"}
    assert all(0 <= value <= 100 for value in result.values())
    assert time.monotonic() - start < 1


def test_desktop_tools_require_exact_session_turn_and_single_use(registry):
    args = {"app": "Calculator"}
    action = registry.request("session-a", "turn-a", "open_app", args)
    args["app"] = "Safari"
    assert "Calculator" in action.arguments_json
    with pytest.raises(PermissionError):
        registry.read("open_app", {"app": "Calculator"})
    with pytest.raises(PermissionError):
        registry.decide(action.id, "session-b", "turn-a", True)
    with pytest.raises(PermissionError):
        registry.decide(action.id, "session-a", "turn-b", True)
    assert registry.decide(action.id, "session-a", "turn-a", False)["status"] == "denied"
    with pytest.raises(PermissionError):
        registry.decide(action.id, "session-a", "turn-a", True)


def test_cancellation_invalidates_actions_and_disabled_tools(registry):
    action = registry.request("s", "t", "web_search", {"query": "local inference"})
    registry.invalidate("t")
    with pytest.raises(PermissionError):
        registry.decide(action.id, "s", "t", True)
    registry.settings.tools.enabled = []
    with pytest.raises(PermissionError):
        registry.request("s", "t", "web_search", {"query": "anything"})
    assert registry.definitions() == []


def test_approval_expiry_uses_elapsed_time(registry):
    registry.settings.tools.approval_seconds = 10
    action = registry.request("s", "t", "open_app", {"app": "Calculator"})
    time.sleep(10.02)
    with pytest.raises(PermissionError, match="expired"):
        registry.decide(action.id, "s", "t", True)
    assert action.id not in registry.pending


@pytest.mark.parametrize(
    "name,args",
    [
        ("open_app", {"app": "Terminal"}),
        ("open_app", {"app": "Calculator", "extra": "command"}),
        ("browse_website", {"url": "file:///etc/passwd"}),
        ("browse_website", {"url": "https://user:pass@example.org"}),
        ("web_search", {"query": ["not", "text"]}),
    ],
)
def test_argument_validation(registry, name, args):
    with pytest.raises((ValueError, PermissionError)):
        registry.request("s", "t", name, args)


def test_tool_parser_never_evaluates_python(tmp_path):
    marker = tmp_path / "should-not-exist"
    malicious = (
        f'<|tool_call_start|>[__import__("pathlib").Path("{marker}").touch()]<|tool_call_end|>'
    )
    with pytest.raises(ValueError):
        parse_calls(malicious)
    assert not marker.exists()
    calls = parse_calls('<|tool_call_start|>[open_app(app="Calculator")]<|tool_call_end|>')
    assert calls[0]["name"] == "open_app"


def test_qwen35_xml_style_calls_are_data():
    calls = parse_calls(
        "<tool_call>\n<function=open_app>\n<parameter=app>Calculator</parameter>\n</function>\n</tool_call>"
    )
    assert calls == [{"name": "open_app", "arguments": '{"app": "Calculator"}'}]
    for invalid in [
        "<tool_call><function=x><parameter=a>v</parameter><parameter=a>w</parameter></function></tool_call>",
        '<tool_call>{"name":"x","arguments":{}}',
    ]:
        with pytest.raises(ValueError):
            parse_calls(invalid)

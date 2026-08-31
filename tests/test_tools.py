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


def test_side_effects_cannot_use_the_read_execution_path(registry):
    with pytest.raises(PermissionError):
        registry.read("open_app", {"app": "Calculator"})


def test_disabled_tools_have_no_manifest_or_execution_path(registry):
    registry.settings.tools.enabled = []
    with pytest.raises(PermissionError):
        registry.validate("web_search", {"query": "anything"})
    assert registry.definitions() == []


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
        registry.validate(name, args)


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


@pytest.mark.parametrize(
    "text",
    [
        "Hello, how are you?",
        "Well,",
        "What else can you do for me?",
        "What is the capital of France?",
        "How do I open Safari?",
        "Don't open Safari.",
        "Explain the phrase 'open Safari'.",
        "A document says: open Safari.",
        "Do that again.",
        "What is weather?",
        "What is time dilation?",
        "Create a file with my notes.",
        "Delete everything on my desktop.",
    ],
)
def test_ordinary_or_ambiguous_text_cannot_request_actions(registry, text):
    assert registry.definitions(text) == []
    with pytest.raises(PermissionError, match="explicit request"):
        registry.validate_request(text, "open_app", {"app": "Safari"})


@pytest.mark.parametrize(
    "text,name,args",
    [
        ("Please open the Calculator app for me.", "open_app", {"app": "Calculator"}),
        ("Can you launch Notes on my Mac?", "open_app", {"app": "Notes"}),
        (
            "Open https://example.org in the browser.",
            "browse_website",
            {"url": "https://example.org"},
        ),
        ("Search the web for sourdough recipes.", "web_search", {"query": "sourdough recipes"}),
        ("Check the weather in Chicago.", "weather", {"location": "Chicago"}),
        ("Save a screenshot of my screen.", "screenshot", {}),
        ("What time is now?", "local_time", {}),
        ("Show current CPU, memory and disk usage.", "system_info", {}),
        (
            "Find the installation guide in my knowledge base.",
            "rag_search",
            {"query": "installation guide"},
        ),
    ],
)
def test_request_scopes_tools_and_cannot_repeat(registry, text, name, args):
    definitions = registry.definitions(text)
    assert [d["function"]["name"] for d in definitions] == [name]
    registry.validate_request(text, name, args)
    assert registry.definitions(text, {name}) == []


def test_requested_target_cannot_be_substituted(registry):
    with pytest.raises(PermissionError, match="explicit request"):
        registry.validate_request("Open Calculator.", "open_app", {"app": "Safari"})
    with pytest.raises(PermissionError, match="explicit request"):
        registry.validate_request(
            "Open https://example.org", "browse_website", {"url": "https://evil.invalid"}
        )
    definitions = registry.definitions("Open Calculator.")
    assert definitions[0]["function"]["parameters"]["properties"]["app"]["enum"] == ["Calculator"]


def test_local_time_comes_from_actual_clock(registry):
    from datetime import datetime

    before = datetime.now().astimezone()
    result = registry.read("local_time", {})
    after = datetime.now().astimezone()
    assert before <= datetime.fromisoformat(result["datetime"]) <= after
    assert result["source"] == "mac_clock"

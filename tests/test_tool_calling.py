import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

import macbot.config as config
import macbot.tools as tools
import macbot.voice_assistant as va


def _fake_post_factory(payload):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    def _post(url, json=None, timeout=0):  # noqa: D401 - mimic requests.post
        return Resp()

    return _post


def test_llama_chat_invokes_enabled_tool(monkeypatch):
    """If the LLM requests a tool and it is enabled, the tool should run."""

    monkeypatch.setattr(config, "get_enabled_tools", lambda: ["web_search"])

    called = {}

    def fake_search(query):
        called["query"] = query
        return "result"

    monkeypatch.setattr(tools, "web_search", fake_search)
    monkeypatch.setattr(tools.TOOL_REGISTRY["web_search"], "func", fake_search)

    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "web_search",
                                "arguments": json.dumps({"query": "hello"}),
                            }
                        }
                    ]
                }
            }
        ]
    }

    monkeypatch.setattr(va.requests, "post", _fake_post_factory(payload))

    out = va.llama_chat("search for hello")
    assert out == "result"
    assert called["query"] == "hello"


def test_llama_chat_rejects_disabled_tool(monkeypatch):
    """Disabled tools should not execute even if requested by the LLM."""

    monkeypatch.setattr(config, "get_enabled_tools", lambda: [])

    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "web_search",
                                "arguments": json.dumps({"query": "hi"}),
                            }
                        }
                    ]
                }
            }
        ]
    }

    monkeypatch.setattr(va.requests, "post", _fake_post_factory(payload))

    out = va.llama_chat("search for hi")
    assert "disabled" in out.lower()


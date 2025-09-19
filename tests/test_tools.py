import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot import tools


def test_get_system_info_returns_promptly():
    start = time.perf_counter()
    info = tools.get_system_info()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"get_system_info took too long: {elapsed}s"
    assert info.startswith("System Status:"), info


def test_rag_search_sends_authorization_header(monkeypatch):
    token = "test-token-123"
    captured = {}

    def fake_get(url, *args, **kwargs):
        class _Response:
            status_code = 200

            def json(self):
                return {"status": "ok"}

        return _Response()

    def fake_post(url, *args, **kwargs):
        captured["headers"] = kwargs.get("headers")

        class _Response:
            status_code = 200

            def json(self):
                return {
                    "results": [
                        {
                            "metadata": {"title": "Doc"},
                            "content": "Some relevant content",
                        }
                    ]
                }

        return _Response()

    monkeypatch.setattr(tools.cfg, "get_rag_api_tokens", lambda: [token])
    monkeypatch.setattr(tools.requests, "get", fake_get)
    monkeypatch.setattr(tools.requests, "post", fake_post)

    result = tools.rag_search("query")

    assert "Top results" in result
    assert captured["headers"]["Authorization"] == f"Bearer {token}"

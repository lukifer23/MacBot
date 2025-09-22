import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from macbot import auth


def test_verify_api_token_accepts_rag_tokens(monkeypatch):
    """RAG API tokens should be accepted even when no primary tokens are set."""

    # Ensure a clean environment so only MACBOT_RAG_API_TOKENS is populated
    monkeypatch.delenv("MACBOT_API_TOKENS", raising=False)
    monkeypatch.delenv("MACBOT_RAG_API_TOKENS", raising=False)
    monkeypatch.setattr(auth, "_auth_manager_instance", None)

    rag_tokens = ["rag-token-alpha", "rag-token-beta"]
    monkeypatch.setenv("MACBOT_RAG_API_TOKENS", ", ".join(rag_tokens))

    manager = auth.AuthenticationManager()

    for token in rag_tokens:
        assert manager.verify_api_token(token)

    # Placeholder tokens from configuration should be ignored
    assert not manager.verify_api_token("change-me")
    assert not manager.verify_api_token("not-a-token")

    # Existing behavior for MACBOT_API_TOKENS should remain unchanged
    monkeypatch.setenv("MACBOT_API_TOKENS", "primary-token")
    monkeypatch.delenv("MACBOT_RAG_API_TOKENS", raising=False)
    monkeypatch.setattr(auth, "_auth_manager_instance", None)

    refreshed_manager = auth.AuthenticationManager()
    assert refreshed_manager.verify_api_token("primary-token")

import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot import auth, config as cfg


def _clear_token_env(monkeypatch):
    for key in list(os.environ.keys()):
        if key.startswith("MACBOT_API_TOKEN") or key.startswith("MACBOT_API_TOKENS"):
            monkeypatch.delenv(key, raising=False)
        if key.startswith("MACBOT_RAG_API_TOKEN") or key.startswith("MACBOT_RAG_API_TOKENS"):
            monkeypatch.delenv(key, raising=False)


def test_auth_manager_loads_and_logs_all_tokens(monkeypatch, caplog):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("MACBOT_JWT_SECRET", "unit-test-secret")
    monkeypatch.setenv("MACBOT_API_TOKENS", "alpha, beta ")
    monkeypatch.setenv("MACBOT_RAG_API_TOKENS", " gamma ")
    monkeypatch.setenv("MACBOT_RAG_API_TOKEN_2", "delta")
    monkeypatch.setenv("MACBOT_RAG_API_TOKEN_1", "epsilon")

    caplog.set_level(logging.INFO, logger="macbot.auth")

    manager = auth.AuthenticationManager()

    expected_tokens = {"alpha", "beta", "gamma", "delta", "epsilon"}
    hashed_expected = {manager.hash_api_token(token) for token in expected_tokens}

    assert manager._api_tokens == hashed_expected
    for token in expected_tokens:
        assert manager.verify_api_token(token)
        assert token in caplog.text

    assert "Active API tokens" in caplog.text


def test_get_rag_api_tokens_includes_numbered(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("MACBOT_RAG_API_TOKENS", "alpha, beta")
    monkeypatch.setenv("MACBOT_RAG_API_TOKEN_2", "delta")
    monkeypatch.setenv("MACBOT_RAG_API_TOKEN_1", "gamma")

    tokens = cfg.get_rag_api_tokens()

    assert tokens == ["alpha", "beta", "gamma", "delta"]

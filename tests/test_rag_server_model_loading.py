"""Tests for RAGServer SentenceTransformer loading logic."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


class _DummyCollection:
    def add(self, *args, **kwargs):  # pragma: no cover - simple stub
        return None

    def query(self, *args, **kwargs):  # pragma: no cover - simple stub
        return {"documents": [[]], "ids": [[]], "metadatas": [[]], "distances": [[]]}

    def delete(self, *args, **kwargs):  # pragma: no cover - simple stub
        return None


class _DummyPersistentClient:
    def __init__(self, *args, **kwargs):  # pragma: no cover - simple stub
        self._collection = _DummyCollection()

    def get_or_create_collection(self, *args, **kwargs):  # pragma: no cover - simple stub
        return self._collection


def _prepare_rag_import(monkeypatch: pytest.MonkeyPatch, local_path: str, repo_id: str) -> None:
    """Stub heavy dependencies and configure embedding getters before importing."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    import macbot.config as config_module

    monkeypatch.setattr(config_module, "get_sentence_transformer_local_path", lambda: local_path)
    monkeypatch.setattr(config_module, "get_sentence_transformer_repo_id", lambda: repo_id)

    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        types.SimpleNamespace(PersistentClient=_DummyPersistentClient),
    )


def test_rag_server_prefers_local_sentence_transformer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_model_dir = tmp_path / "model"
    local_model_dir.mkdir()

    calls: dict[str, object] = {}

    class DummySentenceTransformer:  # pragma: no cover - simple stub
        def __init__(self, model_name_or_path, **kwargs):
            calls["args"] = (model_name_or_path,)
            calls["kwargs"] = kwargs

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=DummySentenceTransformer),
    )

    _prepare_rag_import(monkeypatch, str(local_model_dir), "sentence-transformers/fallback")

    sys.modules.pop("macbot.rag_server", None)
    import macbot.rag_server as rag_module

    assert Path(calls["args"][0]) == local_model_dir
    assert calls["kwargs"].get("local_files_only") is True
    assert isinstance(rag_module.rag_server.embedding_model, DummySentenceTransformer)


def test_rag_server_missing_local_model_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing"

    class DummySentenceTransformer:  # pragma: no cover - simple stub
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=DummySentenceTransformer),
    )

    _prepare_rag_import(monkeypatch, str(missing_dir), "sentence-transformers/fallback")

    sys.modules.pop("macbot.rag_server", None)
    with pytest.raises(RuntimeError) as exc:
        import macbot.rag_server  # noqa: F401

    assert "local_path" in str(exc.value)
    assert "models.embedding.sentence_transformer.local_path" in str(exc.value)


def test_rag_server_uses_repo_when_no_local_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    class DummySentenceTransformer:  # pragma: no cover - simple stub
        def __init__(self, model_name_or_path, **kwargs):
            calls["args"] = (model_name_or_path,)
            calls["kwargs"] = kwargs

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=DummySentenceTransformer),
    )

    _prepare_rag_import(monkeypatch, "", "sentence-transformers/offline-copy")

    sys.modules.pop("macbot.rag_server", None)
    import macbot.rag_server as rag_module

    assert calls["args"][0] == "sentence-transformers/offline-copy"
    assert calls["kwargs"].get("local_files_only") is False
    assert isinstance(rag_module.rag_server.embedding_model, DummySentenceTransformer)


"""Tests for the RAG server Flask API validation behavior."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def rag_server_client(monkeypatch: pytest.MonkeyPatch) -> Iterator["FlaskClient"]:
    """Provide a Flask test client with heavy dependencies stubbed out."""
    from flask.testing import FlaskClient

    class DummyCollection:
        def add(self, *args, **kwargs):  # pragma: no cover - simple stub
            return None

        def query(self, *args, **kwargs):  # pragma: no cover - simple stub
            return {"documents": [[]], "ids": [[]], "metadatas": [[]], "distances": [[]]}

        def delete(self, *args, **kwargs):  # pragma: no cover - simple stub
            return None

    class DummyPersistentClient:
        def __init__(self, *args, **kwargs):  # pragma: no cover - simple stub
            self._collection = DummyCollection()

        def get_or_create_collection(self, *args, **kwargs):  # pragma: no cover - simple stub
            return self._collection

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))

    import macbot.config as config_module

    monkeypatch.setattr(config_module, "get_sentence_transformer_local_path", lambda: "")
    monkeypatch.setattr(config_module, "get_sentence_transformer_repo_id", lambda: "sentence-transformers/test-model")

    monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace(PersistentClient=DummyPersistentClient))

    class DummySentenceTransformer:  # pragma: no cover - simple stub
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=DummySentenceTransformer),
    )

    sys.modules.pop("macbot.rag_server", None)
    import macbot.rag_server as rag_server_module

    class DummyRAGServer:  # pragma: no cover - simple stub
        def search(self, query: str, top_k: int = 5):
            return []

        def add_document(self, content: str, title: str, doc_type: str, metadata):
            return "dummy-id"

        def list_documents(self):
            return []

    monkeypatch.setattr(rag_server_module, "rag_server", DummyRAGServer())
    monkeypatch.setattr(rag_server_module.auth_manager, "verify_api_token", lambda token: True)
    rag_server_module.app.config["TESTING"] = True

    with rag_server_module.app.test_client() as client:
        client.environ_base["HTTP_AUTHORIZATION"] = "Bearer test-token"
        yield client


def _assert_validation_error(response, message_substring: str) -> None:
    payload = response.get_json()
    assert response.status_code == 400
    assert payload["code"] == "validation_error"
    assert message_substring in payload["error"]


def test_search_requires_json_body_object(rag_server_client):
    response = rag_server_client.post(
        "/api/search",
        data="",
        content_type="application/json",
    )
    _assert_validation_error(response, "JSON object")


def test_search_rejects_non_mapping_payload(rag_server_client):
    response = rag_server_client.post(
        "/api/search",
        json=["invalid"],
    )
    _assert_validation_error(response, "JSON object")


def test_add_document_requires_json_body_object(rag_server_client):
    response = rag_server_client.post(
        "/api/documents",
        data="",
        content_type="application/json",
    )
    _assert_validation_error(response, "JSON object")


def test_add_document_rejects_non_mapping_payload(rag_server_client):
    response = rag_server_client.post(
        "/api/documents",
        json=["invalid"],
    )
    _assert_validation_error(response, "JSON object")


def test_add_document_rejects_non_mapping_metadata(rag_server_client):
    response = rag_server_client.post(
        "/api/documents",
        json={
            "content": "hello world",
            "title": "Test",
            "metadata": ["not", "a", "dict"],
        },
    )
    _assert_validation_error(response, "Metadata")

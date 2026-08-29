"""Real ONNX embeddings and transactional exact-vector stores."""

import json
from pathlib import Path

import numpy as np
import pytest

from macbot.config import Settings, load, prepare
from macbot.rag_server import create_app
from macbot.retrieval import DocumentStore

pytestmark = pytest.mark.models


@pytest.fixture
def documents(tmp_path):
    source = load().data_dir / "models/minilm"
    assert source.is_dir(), "Provision minilm before running the supported test suite"
    settings = Settings(data_dir=tmp_path / "runtime")
    prepare(settings)
    (settings.data_dir / "models/minilm").symlink_to(source, target_is_directory=True)
    store = DocumentStore(settings)
    yield settings, store
    store.close()


def test_real_retrieval_dedup_chunk_provenance_and_delete(documents):
    _, store = documents
    assert store.list() == []
    assert store.search("anything") == []
    content = "The service access code is cobalt lantern. " * 100
    doc = store.add(content, "Service notes", metadata={"source": "test"})
    assert store.add(content, "Service notes") == doc
    result = store.search("What is the service access code?")
    assert result[0]["metadata"]["document_id"] == doc
    assert store.stats()["chunks"] > 1
    for chunk in result:
        meta = chunk["metadata"]
        assert chunk["content"] == content[meta["start"] : meta["end"]]
        assert len(store.embedder.tokenizer.encode(chunk["content"]).ids) <= 256
    assert store.get(doc)["metadata"]["source"] == "test"
    assert store.delete(doc)
    assert not store.delete(doc)
    assert store.search("access code") == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "not an object",
        {"content": "abc", "title": "T", "metadata": []},
        {"content": "abc", "title": "T", "type": []},
        {"content": "", "title": "T"},
    ],
)
def test_add_document_rejects_malformed_payload(documents, payload):
    settings, store = documents
    app = create_app(settings, store)
    auth = app.extensions["macbot_auth"]
    try:
        response = app.test_client().post(
            settings.services.rag.url + "/api/documents",
            data=json.dumps(payload),
            content_type="application/json",
            headers=auth.headers("rag"),
        )
        assert response.status_code == 400
        assert store.list() == []
    finally:
        auth.close()


@pytest.mark.parametrize(
    "payload", [[], None, {}, {"query": "hello", "top_k": "5"}, {"query": "hello", "top_k": False}]
)
def test_search_rejects_malformed_payload(documents, payload):
    settings, store = documents
    app = create_app(settings, store)
    auth = app.extensions["macbot_auth"]
    try:
        response = app.test_client().post(
            settings.services.rag.url + "/api/search",
            data=json.dumps(payload),
            content_type="application/json",
            headers=auth.headers("rag"),
        )
        assert response.status_code == 400
    finally:
        auth.close()


def test_authenticated_embedding_endpoint_returns_real_normalized_vectors(documents):
    settings, store = documents
    app = create_app(settings, store)
    auth = app.extensions["macbot_auth"]
    try:
        response = app.test_client().post(
            settings.services.rag.url + "/api/embed",
            json={"texts": ["cobalt", "ocean blue"]},
            headers=auth.headers("rag"),
        )
        assert response.status_code == 200
        vectors = np.asarray(response.get_json()["vectors"], dtype=np.float32)
        assert vectors.shape == (2, 384)
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
    finally:
        auth.close()


def test_migration_preserves_ids_duplicates_and_backup(documents, tmp_path):
    _, store = documents
    source = tmp_path / "legacy"
    source.mkdir()
    payload = {
        "documents": {"original-1": "A preserved source.", "original-2": "A preserved source."},
        "metadata": {"original-1": {"title": "Title"}, "original-2": {"title": "Title"}},
    }
    original = json.dumps(payload).encode()
    (source / "documents.json").write_bytes(original)
    report = store.migrate(source)
    assert {d["id"] for d in store.list()} == {"original-1", "original-2"}
    assert (Path(report["source_backup"]) / "documents.json").read_bytes() == original
    assert (source / "documents.json").read_bytes() == original
    assert (Path(report["rollback_backup"]) / "documents.sqlite3").is_file()
    payload["documents"]["original-1"] = "Conflicting source"
    (source / "documents.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="conflict"):
        store.migrate(source)
    assert store.get("original-1")["content"] == "A preserved source."
    assert store.stats()["documents"] == 2


def test_rebuild_changes_index_and_keeps_previous(documents):
    _, store = documents
    store.add("Saturn is a planet with rings.", "Astronomy")
    previous = store.active_name
    store.rebuild()
    assert store.active_name != previous
    assert store.revision_count(previous) == 1
    assert "Saturn" in store.search("Which planet has rings?")[0]["content"]


def test_missing_derived_index_is_backed_up_and_rebuilt(tmp_path):
    source = load().data_dir / "models/minilm"
    settings = Settings(data_dir=tmp_path / "runtime")
    prepare(settings)
    (settings.data_dir / "models/minilm").symlink_to(source, target_is_directory=True)
    store = DocumentStore(settings)
    document_id = store.add("The recovery phrase is cobalt lantern.", "Recovery")
    missing_revision = store.active_name
    assert missing_revision
    store.close()

    (settings.data_dir / "rag" / "indexes" / missing_revision / "vectors.npy").unlink()
    recovered = DocumentStore(settings)
    try:
        assert recovered.active_name != missing_revision
        assert recovered.recovery_backup is not None
        backup = recovered.recovery_backup
        assert (backup / "documents.sqlite3").is_file()
        assert not (backup / "indexes" / missing_revision / "vectors.npy").exists()
        assert recovered.get(document_id)["content"] == "The recovery phrase is cobalt lantern."
        assert (
            recovered.search("What is the recovery phrase?")[0]["metadata"]["document_id"]
            == document_id
        )
    finally:
        recovered.close()


def test_restore_preserves_previous_store_and_restores_source(documents):
    import os
    import sqlite3
    import subprocess
    import sys

    from macbot.retrieval import restore

    settings, store = documents
    store.add("The original preserved content.", "Original")
    backup = store.backup()
    store.add("A document added after the backup.", "Later")
    with pytest.raises(RuntimeError, match="Stop"):
        restore(settings, backup)
    store.close()
    previous = restore(settings, backup)
    with sqlite3.connect(previous / "documents.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    # New process matches CLI startup and proves the memory-mapped index reopens.
    script = 'from macbot.config import load; from macbot.retrieval import DocumentStore; s=DocumentStore(load()); print(s.stats()["documents"]); print(s.search("original preserved content")[0]["content"]); s.close()'
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "MACBOT_DATA_DIR": str(settings.data_dir),
            "MACBOT_CONFIG": str(settings.config_path),
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip().splitlines() == ["1", "The original preserved content."]

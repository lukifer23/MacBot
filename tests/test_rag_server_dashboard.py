import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from macbot import rag_server as rag_module


class DummyRAGServer:
    def __init__(self):
        self.documents = {}
        self.document_metadata = {}

    def add_document(self, content, title, doc_type="text", metadata=None):
        doc_id = f"doc_{len(self.documents)}"
        self.documents[doc_id] = content
        self.document_metadata[doc_id] = {
            "title": title,
            "type": doc_type,
            "added": "2024-01-01T00:00:00",
            "length": len(content),
            **(metadata or {}),
        }
        return doc_id

    def list_documents(self):
        return [
            {
                "id": doc_id,
                "title": meta.get("title", "Untitled"),
                "type": meta.get("type", "unknown"),
                "added": meta.get("added", ""),
                "length": meta.get("length", 0),
            }
            for doc_id, meta in self.document_metadata.items()
        ]

    def get_stats(self):
        count = len(self.documents)
        return {
            "total_documents": count,
            "total_chunks": count,
            "embedding_model": "test-model",
            "vector_db": "test-db",
            "last_updated": "2024-01-01T00:00:00",
        }


@pytest.fixture
def client(monkeypatch):
    dummy = DummyRAGServer()
    monkeypatch.setattr(rag_module, "_rag_server", dummy)
    with rag_module.app.test_client() as test_client:
        yield test_client, dummy


def test_dashboard_escapes_document_titles(client):
    test_client, server = client
    server.add_document(
        content="<script>alert('content')</script>",
        title='<script>alert("xss")</script>',
        doc_type="malicious",
    )

    response = test_client.get("/")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    assert "&lt;script&gt;alert(&#34;xss&#34;)&lt;/script&gt;" in body
    assert "<strong><script>alert(\"xss\")</script></strong>" not in body

"""Authenticated local document service. No import-time database or model loading."""

import os
from typing import Any, cast

from flask import Flask, jsonify, request

from .auth import AuthStore, install_security
from .config import Settings, load
from .retrieval import DocumentStore
from .validation import json_object


def create_app(settings: Settings, store: DocumentStore | None = None) -> Flask:
    app = Flask(__name__)
    auth = AuthStore(settings.data_dir)
    documents = store or DocumentStore(settings)
    app.extensions.update(macbot_auth=auth, macbot_documents=documents)
    install_security(app, settings, "rag", auth)

    @app.get("/health")
    def health():
        return jsonify(status="alive", service="rag")

    @app.get("/ready")
    def ready():
        return jsonify(status="ready", pid=os.getpid(), **documents.stats())

    @app.route("/api/documents", methods=["GET", "POST"])
    def collection():
        if request.method == "GET":
            return jsonify(documents=documents.list())
        data = json_object()
        doc_id = documents.add(
            data.get("content", ""),
            data.get("title", ""),
            data.get("type", "text"),
            data.get("metadata"),
        )
        return jsonify(id=doc_id, success=True), 201

    @app.post("/api/documents/batch")
    def add_batch():
        data = json_object()
        payload = data.get("documents")
        if not isinstance(payload, list):
            raise ValueError("documents must be a list")
        ids = documents.add_many(cast(list[dict[str, Any]], payload))
        return jsonify(ids=ids, success=True), 201

    @app.post("/api/documents/batch-delete")
    def delete_batch():
        data = json_object()
        payload = data.get("ids")
        if not isinstance(payload, list):
            raise ValueError("ids must be a list")
        deleted = documents.delete_many(cast(list[str], payload))
        return jsonify(deleted=deleted, success=True)

    @app.route("/api/documents/<doc_id>", methods=["GET", "DELETE"])
    def document(doc_id):
        if request.method == "DELETE":
            return (
                (jsonify(success=True), 200)
                if documents.delete(doc_id)
                else (jsonify(error="Not found"), 404)
            )
        value = documents.get(doc_id)
        return jsonify(value) if value else (jsonify(error="Not found"), 404)

    @app.post("/api/search")
    def search():
        data = json_object()
        return jsonify(
            documents.search_response(
                data.get("query", ""),
                data.get("top_k", 5),
                min_score=data.get("min_score", 0.30),
            )
        )

    @app.post("/api/embed")
    def embed():
        data = json_object()
        texts = data.get("texts")
        if not isinstance(texts, list):
            raise ValueError("Embedding input must be a list")
        return jsonify(vectors=documents.embed(texts))

    @app.get("/api/stats")
    def stats():
        return jsonify(documents.stats())

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc), code="invalid_request"), 400

    return app


def main():
    from .service_lifecycle import termination_cleanup

    termination_cleanup()
    settings = load()
    app = create_app(settings)
    try:
        app.run(
            host=settings.services.rag.host,
            port=settings.services.rag.port,
            threaded=True,
            use_reloader=False,
        )
    finally:
        app.extensions["macbot_documents"].close()
        app.extensions["macbot_auth"].close()


if __name__ == "__main__":
    main()

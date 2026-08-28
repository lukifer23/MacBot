"""Authenticated local document service. No import-time database or model loading."""

import os

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
        return jsonify(results=documents.search(data.get("query", ""), data.get("top_k", 5)))

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

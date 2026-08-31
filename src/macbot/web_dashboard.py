"""Authenticated, read-only developer diagnostics for MacBot.

The SwiftUI application is the sole operator surface. This service intentionally
exposes no conversation, audio, document, settings, approval, action, or service
lifecycle mutations.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from flask import Flask, g, jsonify, render_template, request

from .auth import COOKIE, AuthStore, install_security
from .config import Settings, load
from .validation import json_object


def create_app(settings: Settings) -> Flask:
    app = Flask(__name__)
    auth = AuthStore(settings.data_dir)
    install_security(app, settings, "dashboard", auth, browser=True)
    client = httpx.Client(timeout=5, trust_env=False)
    app.extensions.update(macbot_auth=auth, macbot_client=client)

    def proxy(service: str, path: str):
        response = client.get(
            settings.endpoint(service).url + path,
            headers={**auth.headers(service), "X-MacBot-Session": g.principal},
        )
        payload: Any = response.json()
        return jsonify(payload), response.status_code

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/health")
    def health():
        return jsonify(status="alive", service="diagnostics")

    @app.get("/ready")
    def ready():
        return jsonify(status="ready", pid=os.getpid(), mode="read_only")

    @app.post("/auth/exchange")
    def exchange():
        data = json_object()
        token = data.get("token", "") if isinstance(data, dict) else ""
        if not isinstance(token, str) or len(token) > 128:
            return jsonify(error="Invalid login code"), 400
        result = auth.exchange(token)
        if not result:
            return jsonify(error="Login code expired or already used. Run macbot open again."), 401
        session, csrf = result
        response = jsonify(csrf=csrf)
        response.set_cookie(
            COOKIE, session, max_age=8 * 3600, httponly=True, samesite="Strict", path="/"
        )
        return response

    @app.post("/auth/logout")
    def logout():
        auth.revoke(request.cookies.get(COOKIE, ""))
        response = jsonify(success=True)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/auth/session")
    def resume_session():
        csrf = auth.resume(request.cookies.get(COOKIE, ""))
        if csrf is None:
            return jsonify(error="Authentication required", code="unauthorized"), 401
        return jsonify(csrf=csrf)

    @app.get("/api/status")
    def status():
        return proxy("assistant", "/info")

    @app.get("/api/services")
    @app.get("/api/metrics")
    @app.get("/api/pipeline-check")
    def services():
        return proxy("orchestrator", "/status")

    @app.get("/api/diagnostics")
    def diagnostics():
        assistant, assistant_code = proxy("assistant", "/info")
        supervisor, supervisor_code = proxy("orchestrator", "/status")
        return jsonify(
            mode="read_only",
            assistant_status=assistant_code,
            supervisor_status=supervisor_code,
            assistant=assistant.get_json(),
            supervisor=supervisor.get_json(),
        )

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc), code="invalid_request"), 400

    @app.errorhandler(httpx.HTTPError)
    def downstream(exc):
        return jsonify(error="Local service unavailable", code="service_unavailable"), 503

    return app


def main():
    settings = load()
    app = create_app(settings)
    app.run(
        host=settings.services.dashboard.host,
        port=settings.services.dashboard.port,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()

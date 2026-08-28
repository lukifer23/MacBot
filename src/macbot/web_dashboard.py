"""Same-origin dashboard. All inference and action policy live in the assistant."""

from __future__ import annotations

import os
import threading
from urllib.parse import urlencode

import httpx
from flask import Flask, g, jsonify, render_template, request
from flask_socketio import SocketIO

from .auth import COOKIE, AuthStore, install_security
from .config import Settings, load, save
from .provision import model_dir, voices
from .validation import json_object


def create_app(settings: Settings) -> Flask:
    app = Flask(__name__)
    auth = AuthStore(settings.data_dir)
    install_security(app, settings, "dashboard", auth, browser=True)
    origins = {
        f"http://{host}:{settings.services.dashboard.port}"
        for host in ("127.0.0.1", "localhost", "[::1]")
    }
    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins=list(origins),
        max_http_buffer_size=64 * 1024,
        logger=False,
        engineio_logger=False,
    )
    client = httpx.Client(timeout=25, trust_env=False)
    sessions: dict[str, str] = {}
    sessions_lock = threading.Lock()
    app.extensions.update(
        macbot_auth=auth,
        macbot_client=client,
        macbot_socket_sessions=sessions,
        macbot_socket_lock=sessions_lock,
    )

    def proxy(service, path, method="GET", payload=None):
        response = client.request(
            method,
            settings.endpoint(service).url + path,
            headers={**auth.headers(service), "X-MacBot-Session": g.principal},
            json=payload,
        )
        return jsonify(response.json()), response.status_code

    @app.get("/")
    def index():
        return render_template("dashboard.html")

    @app.get("/health")
    def health():
        return jsonify(status="alive", service="dashboard")

    @app.get("/ready")
    def ready():
        return jsonify(status="ready", pid=os.getpid())

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

    @app.get("/api/status")
    def status():
        return proxy("assistant", "/info")

    @app.get("/api/services")
    @app.get("/api/metrics")
    @app.get("/api/pipeline-check")
    def services():
        return proxy("orchestrator", "/status")

    @app.get("/api/events")
    def events():
        return proxy(
            "assistant",
            "/events?"
            + urlencode(
                {
                    "after": max(0, int(request.args.get("after", "0"))),
                    **({"epoch": request.args["epoch"]} if "epoch" in request.args else {}),
                }
            ),
        )

    routes = {
        "chat": "/chat",
        "llm": "/chat",
        "voice": "/voice",
        "browser-recording": "/browser-recording",
        "interrupt": "/interrupt",
        "listen": "/listen",
        "approve": "/approve",
        "clear": "/clear",
        "assistant-speak": "/speak",
        "preview-voice": "/speak",
    }
    for name, target in routes.items():

        def handler(target=target):
            return proxy("assistant", target, "POST", json_object())

        app.add_url_rule(
            "/api/" + name, endpoint="proxy_" + name, view_func=handler, methods=["POST"]
        )

    @app.post("/api/service/<name>/restart")
    def restart(name):
        if name not in {"llm", "assistant", "rag", "dashboard"}:
            return jsonify(error="Unknown service"), 404
        return proxy("orchestrator", f"/service/{name}/restart", "POST")

    @app.get("/api/documents")
    def documents():
        return proxy("rag", "/api/documents")

    @app.route("/api/documents/<doc_id>", methods=["GET", "DELETE"])
    def document(doc_id):
        return proxy("rag", "/api/documents/" + doc_id, request.method)

    @app.post("/api/search")
    def search():
        return proxy("rag", "/api/search", "POST", json_object())

    @app.post("/api/upload-documents")
    def upload():
        files = request.files.getlist("files")
        if not files or len(files) > 10:
            raise ValueError("Select 1–10 files")
        imported, errors = [], []
        for file in files:
            name = file.filename or ""
            data = file.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Individual document exceeds 8 MiB")
            try:
                from pathlib import Path

                from .document_parser import extract

                text = extract(data, Path(name).suffix.lower())
                response = client.post(
                    settings.services.rag.url + "/api/documents",
                    headers=auth.headers("rag"),
                    json={"content": text, "title": name, "type": name.rsplit(".", 1)[-1].lower()},
                )
                response.raise_for_status()
                imported.append({"filename": name, **response.json()})
            except Exception as exc:
                errors.append({"filename": name, "error": str(exc)})
        return jsonify(imported=imported, errors=errors), 200 if not errors else 422

    @app.get("/api/settings")
    def settings_get():
        current = load(settings.config_path)
        return jsonify(
            models=current.models.model_dump(),
            voices=voices(),
        )

    @app.post("/api/settings")
    def settings_set():
        data = json_object()
        if not isinstance(data, dict) or set(data) - {"max_tokens", "tts_speed", "tts_voice"}:
            raise ValueError("Unsupported setting")
        candidate = load(settings.config_path)
        for key, value in data.items():
            setattr(candidate.models, key, value)
        if candidate.models.tts_voice not in voices():
            raise ValueError("Voice is not registered")
        if "tts_voice" in data:
            model_dir(candidate, candidate.models.tts_voice)
        save(candidate)
        return jsonify(success=True, restart_required="assistant")

    @socketio.on("connect")
    def connect(credentials=None):
        allowed = origins
        csrf = credentials.get("csrf", "") if isinstance(credentials, dict) else ""
        if (
            request.host not in {origin.removeprefix("http://") for origin in allowed}
            or request.headers.get("Origin") not in allowed
            or not isinstance(csrf, str)
            or not auth.session(request.cookies.get(COOKIE, ""), csrf)
        ):
            return False
        with sessions_lock:
            sessions[getattr(request, "sid")] = request.cookies[COOKIE]
        return True

    @socketio.on("disconnect")
    def disconnect(reason=None):
        with sessions_lock:
            sessions.pop(getattr(request, "sid"), None)

    # Mutations intentionally use CSRF-protected HTTP. Socket.IO carries events only.
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
    socketio = app.extensions["socketio"]

    def relay():
        after = 0
        epoch = None
        while True:
            try:
                client = app.extensions["macbot_client"]
                response = client.get(
                    settings.services.assistant.url + "/events",
                    params={"after": after, **({"epoch": epoch} if epoch else {})},
                    headers=app.extensions["macbot_auth"].headers("assistant"),
                )
                response.raise_for_status()
                data = response.json()
                after = data["cursor"]
                epoch = data["epoch"]
                with app.extensions["macbot_socket_lock"]:
                    sessions = list(app.extensions["macbot_socket_sessions"].items())
                for sid, token in sessions:
                    if not app.extensions["macbot_auth"].session(token):
                        socketio.server.disconnect(sid)
                    elif data["events"] or data.get("reset"):
                        socketio.emit("turn_events", data, to=sid)
            except httpx.HTTPError:
                socketio.sleep(1)

    socketio.start_background_task(relay)
    socketio.run(
        app,
        host=settings.services.dashboard.host,
        port=settings.services.dashboard.port,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()

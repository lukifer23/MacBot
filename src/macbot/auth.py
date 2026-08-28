"""Loopback is not authentication: sessions, single-use login, CSRF, service keys."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from flask import Flask, g, jsonify, request

from .config import Settings, atomic_write

COOKIE = "macbot_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStore:
    def __init__(self, root: Path):
        self.path = root / "auth.sqlite3"
        self.lock = threading.RLock()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.path.chmod(0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS login (token TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS session (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
        """)
        self.db.commit()
        keyfile = root / "service-keys.json"
        with (root / ".auth-init.lock").open("a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not keyfile.exists():
                keys = {
                    name: secrets.token_urlsafe(32)
                    for name in ("assistant", "rag", "dashboard", "orchestrator", "llm")
                }
                atomic_write(keyfile, json.dumps(keys).encode())
            if keyfile.stat().st_mode & 0o077:
                raise PermissionError("Service credentials must have mode 0600")
            self.keys = json.loads(keyfile.read_text())

    def issue_login(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("DELETE FROM login WHERE expires < ?", (time.time(),))
            self.db.execute("INSERT INTO login VALUES (?, ?)", (digest(token), time.time() + 60))
        return token

    def exchange(self, token: str) -> tuple[str, str] | None:
        session_token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock, self.db:
            row = self.db.execute(
                "DELETE FROM login WHERE token=? AND expires>? RETURNING token",
                (digest(token), time.time()),
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                "INSERT INTO session VALUES (?,?,?)",
                (digest(session_token), digest(csrf), time.time() + 8 * 3600),
            )
        return session_token, csrf

    def session(self, token: str, csrf: str | None = None) -> bool:
        if not token:
            return False
        with self.lock:
            row = self.db.execute(
                "SELECT csrf FROM session WHERE token=? AND expires>?", (digest(token), time.time())
            ).fetchone()
        return bool(row and (csrf is None or secrets.compare_digest(row[0], digest(csrf))))

    def revoke(self, token: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM session WHERE token=?", (digest(token),))

    def headers(self, service: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.keys[service]}

    def close(self) -> None:
        self.db.close()


def install_security(
    app: Flask, settings: Settings, service: str, store: AuthStore, browser: bool = False
) -> None:
    endpoint = settings.endpoint(service)
    allowed_hosts = {
        f"127.0.0.1:{endpoint.port}",
        f"localhost:{endpoint.port}",
        f"[::1]:{endpoint.port}",
    }
    allowed_origins = {"http://" + host for host in allowed_hosts}
    app.config.update(
        MAX_CONTENT_LENGTH=12 * 1024 * 1024, MAX_FORM_MEMORY_SIZE=512 * 1024, MAX_FORM_PARTS=20
    )

    @app.before_request
    def authenticate():
        if request.host not in allowed_hosts:
            return jsonify(error="Invalid Host", code="invalid_host"), 403
        origin = request.headers.get("Origin")
        if origin and origin not in allowed_origins:
            return jsonify(error="Invalid Origin", code="invalid_origin"), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="Cross-site request denied"), 403
        if request.path == "/health":
            return None
        if browser and (
            request.path in {"/", "/auth/exchange"} or request.path.startswith("/static/")
        ):
            if request.path == "/auth/exchange" and origin not in allowed_origins:
                return jsonify(error="Login requires a same-origin request"), 403
            return None
        header = request.headers.get("Authorization", "")
        if secrets.compare_digest(header, "Bearer " + store.keys[service]):
            # Internal clients never send an Origin header. A browser cannot use service keys.
            if origin:
                return jsonify(error="Service credential used from browser"), 403
            g.principal = "service"
            return None
        if browser and store.session(request.cookies.get(COOKIE, "")):
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not store.session(
                request.cookies.get(COOKIE, ""), request.headers.get("X-CSRF-Token", "")
            ):
                return jsonify(error="Invalid CSRF token", code="csrf"), 403
            g.principal = digest(request.cookies[COOKIE])
            return None
        return jsonify(error="Authentication required", code="unauthorized"), 401

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

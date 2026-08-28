"""Real SQLite sessions and Flask security boundaries, with no auth substitutions."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask, jsonify

from macbot.auth import COOKIE, AuthStore, install_security
from macbot.config import Settings, prepare


@pytest.fixture
def secured(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    app = Flask(__name__)
    install_security(app, settings, "dashboard", auth, browser=True)
    app.add_url_rule("/data", view_func=lambda: jsonify(ok=True), methods=["GET", "POST"])
    yield settings, auth, app.test_client()
    auth.close()


def test_default_deny_and_credentials_are_service_specific(secured):
    s, auth, client = secured
    url = s.services.dashboard.url + "/data"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth.headers("rag")).status_code == 401
    assert client.get(url + "?token=" + auth.keys["dashboard"]).status_code == 401
    assert client.get(url, headers=auth.headers("dashboard")).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.invalid:3000"},
        {"Origin": "https://evil.invalid"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://127.0.0.1:3000"},
    ],
)
def test_service_key_never_bypasses_browser_boundaries(secured, headers):
    s, auth, client = secured
    assert (
        client.get(
            s.services.dashboard.url + "/data", headers={**auth.headers("dashboard"), **headers}
        ).status_code
        == 403
    )


def test_login_is_single_use_and_revocation_and_csrf_apply(secured):
    s, auth, client = secured
    code = auth.issue_login()
    session, csrf = auth.exchange(code)
    assert auth.exchange(code) is None
    client.set_cookie(COOKIE, session, domain="127.0.0.1")
    url = s.services.dashboard.url + "/data"
    assert client.get(url).status_code == 200
    assert client.post(url).status_code == 403
    assert client.post(url, headers={"X-CSRF-Token": "incorrect"}).status_code == 403
    response = client.post(url, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    auth.revoke(session)
    assert client.get(url).status_code == 401


def test_persisted_expired_credentials_fail(secured):
    _, auth, _ = secured
    code = auth.issue_login()
    with sqlite3.connect(auth.path) as db:
        db.execute("UPDATE login SET expires=0")
    assert auth.exchange(code) is None
    session, csrf = auth.exchange(auth.issue_login())
    with sqlite3.connect(auth.path) as db:
        db.execute("UPDATE session SET expires=0")
    assert not auth.session(session, csrf)


def test_concurrent_exchange_has_exactly_one_winner(secured):
    _, auth, _ = secured
    code = auth.issue_login()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(auth.exchange, [code] * 8))
    assert sum(r is not None for r in results) == 1
    assert auth.path.stat().st_mode & 0o077 == 0
    assert (auth.path.parent / "service-keys.json").stat().st_mode & 0o077 == 0

import io
import zipfile

import pytest
from docx import Document

from macbot.auth import COOKIE
from macbot.config import Settings, prepare
from macbot.web_dashboard import create_app


@pytest.fixture
def dashboard(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    app = create_app(settings)
    yield settings, app
    app.extensions["macbot_client"].close()
    app.extensions["macbot_auth"].close()


def login(settings, app):
    auth = app.extensions["macbot_auth"]
    client = app.test_client()
    response = client.post(
        settings.services.dashboard.url + "/auth/exchange",
        json={"token": auth.issue_login()},
        headers={"Origin": settings.services.dashboard.url},
    )
    assert response.status_code == 200
    return client, response.json["csrf"], response.headers["Set-Cookie"]


def test_diagnostics_login_cookie_and_read_only_contract(dashboard):
    settings, app = dashboard
    client, csrf, cookie = login(settings, app)
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie

    routes = {rule.rule: sorted(rule.methods or set()) for rule in app.url_map.iter_rules()}
    assert routes["/api/status"] == ["GET", "HEAD", "OPTIONS"]
    assert routes["/api/services"] == ["GET", "HEAD", "OPTIONS"]
    assert routes["/api/diagnostics"] == ["GET", "HEAD", "OPTIONS"]
    for forbidden in (
        "/api/chat",
        "/api/voice",
        "/api/listen",
        "/api/interrupt",
        "/api/approve",
        "/api/clear",
        "/api/settings",
        "/api/documents",
        "/api/service/assistant/restart",
        "/api/events",
    ):
        assert forbidden not in routes

    response = client.post(
        settings.services.dashboard.url + "/auth/logout",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200


def test_new_tab_resumes_same_site_cookie_but_rejects_cross_site(dashboard):
    settings, app = dashboard
    client = app.test_client()
    auth = app.extensions["macbot_auth"]
    assert client.get(settings.services.dashboard.url + "/auth/session").status_code == 401

    token, original_csrf = auth.exchange(auth.issue_login())
    client.set_cookie(COOKIE, token, domain="127.0.0.1")
    for headers in (
        {"Origin": "https://evil.invalid"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Host": "evil.invalid:3000"},
    ):
        assert (
            client.get(
                settings.services.dashboard.url + "/auth/session", headers=headers
            ).status_code
            == 403
        )
    response = client.get(settings.services.dashboard.url + "/auth/session")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.json["csrf"] == original_csrf


def test_dashboard_html_describes_read_only_developer_scope(dashboard):
    settings, app = dashboard
    response = app.test_client().get(settings.services.dashboard.url + "/")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Developer Diagnostics" in text
    assert "read-only" in text
    assert "chat-form" not in text
    assert "microphone" not in text.casefold()


def test_document_parsers_use_real_bounded_processes():
    from macbot.document_parser import extract

    assert extract(b"Private local text.", ".txt") == "Private local text."
    document = Document()
    document.add_paragraph("A real Word document.")
    data = io.BytesIO()
    document.save(data)
    assert extract(data.getvalue(), ".docx") == "A real Word document."
    with pytest.raises(ValueError):
        extract(b"not a PDF", ".pdf")
    with pytest.raises(ValueError):
        extract(b"hello", ".exe")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("oversized.xml", b"x" * (33 * 1024 * 1024))
    with pytest.raises(ValueError):
        extract(archive.getvalue(), ".docx")

import pytest

from macbot.auth import COOKIE
from macbot.config import Settings, prepare
from macbot.web_dashboard import create_app


@pytest.fixture
def dashboard(tmp_path):
    s = Settings(data_dir=tmp_path)
    prepare(s)
    app = create_app(s)
    yield s, app
    app.extensions["macbot_client"].close()
    app.extensions["macbot_auth"].close()


def test_login_cookie_and_csrf_for_mutations(dashboard):
    s, app = dashboard
    auth = app.extensions["macbot_auth"]
    client = app.test_client()
    code = auth.issue_login()
    url = s.services.dashboard.url
    assert client.post(url + "/auth/exchange", json={"token": code}).status_code == 403
    response = client.post(url + "/auth/exchange", json={"token": code}, headers={"Origin": url})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Strict" in response.headers["Set-Cookie"]
    assert client.post(url + "/api/settings", json={"tts_speed": 1.2}).status_code == 403
    headers = {"X-CSRF-Token": response.json["csrf"]}
    assert (
        client.post(url + "/api/settings", json={"tts_speed": 1.2}, headers=headers).status_code
        == 200
    )
    assert (
        client.post(url + "/api/settings", json={"max_tokens": 120}, headers=headers).status_code
        == 200
    )
    assert client.get(url + "/api/settings").json["models"]["tts_speed"] == 1.2
    assert (
        client.post(
            url + "/api/settings", json={"tts_voice": "/etc/passwd"}, headers=headers
        ).status_code
        == 400
    )
    assert client.post(url + "/auth/logout", headers=headers).status_code == 200
    assert client.get(url + "/api/settings").status_code == 401


def test_socket_connections_require_session_csrf_origin_and_host(dashboard):
    s, app = dashboard
    io = app.extensions["socketio"]
    auth = app.extensions["macbot_auth"]
    origin = s.services.dashboard.url
    client = app.test_client()
    headers = {"Origin": origin, "Host": "127.0.0.1:3000"}
    denied = io.test_client(app, flask_test_client=client, headers=headers)
    assert not denied.is_connected()
    token, csrf = auth.exchange(auth.issue_login())
    client.set_cookie(COOKIE, token, domain="127.0.0.1")
    denied = io.test_client(app, flask_test_client=client, headers=headers, auth={"csrf": "wrong"})
    assert not denied.is_connected()
    denied = io.test_client(
        app,
        flask_test_client=client,
        headers={**headers, "Origin": "https://evil.invalid"},
        auth={"csrf": csrf},
    )
    assert not denied.is_connected()
    accepted = io.test_client(app, flask_test_client=client, headers=headers, auth={"csrf": csrf})
    assert accepted.is_connected()
    assert len(app.extensions["macbot_socket_sessions"]) == 1
    accepted.disconnect()
    assert not app.extensions["macbot_socket_sessions"]


def test_document_parsers_use_real_bounded_processes():
    import io
    import zipfile

    from docx import Document

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

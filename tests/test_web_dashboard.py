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


def test_new_tab_can_resume_valid_cookie_but_not_cross_site(dashboard):
    s, app = dashboard
    url = s.services.dashboard.url
    client = app.test_client()
    auth = app.extensions["macbot_auth"]
    assert client.get(url + "/auth/session").status_code == 401

    token, original_csrf = auth.exchange(auth.issue_login())
    client.set_cookie(COOKIE, token, domain="127.0.0.1")
    for headers in (
        {"Origin": "https://evil.invalid"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Host": "evil.invalid:3000"},
    ):
        assert client.get(url + "/auth/session", headers=headers).status_code == 403
    response = client.get(url + "/auth/session")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.json["csrf"] == original_csrf
    headers = {"X-CSRF-Token": response.json["csrf"]}
    assert (
        client.post(url + "/api/settings", json={"tts_speed": 1.0}, headers=headers).status_code
        == 200
    )
    assert client.post(url + "/auth/logout", headers=headers).status_code == 200
    assert client.get(url + "/auth/session").status_code == 401


def test_browser_event_feed_replays_transcription_over_real_http(tmp_path):
    """Execute the production JS reader against the actual authenticated journal.

    No WebSocket or substitute DOM/service: a real rejected login exercises
    retry, then existing and new speech events must arrive in sequence.
    """
    import json
    import shutil
    import subprocess
    import threading
    from pathlib import Path

    from werkzeug.serving import make_server

    from macbot.runtime import Runtime
    from macbot.voice_assistant import create_app as assistant_app

    node = shutil.which("node")
    assert node, "Node is required to verify the dashboard event reader"
    s = Settings(data_dir=tmp_path)
    prepare(s)
    engine = Runtime(s, load_speech=False)
    # Bind first, then publish the actual port to Host validation before serving.
    app = assistant_app(s, engine)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    s.services.assistant.port = server.server_port
    app = assistant_app(s, engine)
    server.app = app
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    engine.events.publish(
        "browser", "speech", "running", "transcription", text="Hello, how are you?"
    )
    engine.events.publish("browser", "speech", "running", "user", text="Hello, how are you?")
    feed = Path(__file__).parents[1] / "src/macbot/static/event-feed.js"
    script = """
const {MacBotEventFeed} = require(process.argv[1]);
const config = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let attempts = 0, failures = 0, inflight = 0, maximum = 0, seen = [];
const timeout = setTimeout(() => {console.error('Event replay timed out'); process.exit(1);}, 8000);
const feed = new MacBotEventFeed(async (after, epoch, signal) => {
  maximum = Math.max(maximum, ++inflight);
  try {
    const response = await fetch(config.url + '/events?after=' + after + (epoch ? '&epoch=' + epoch : ''), {
      headers: {Authorization: attempts++ === 0 ? 'Bearer wrong' : config.authorization}, signal
    });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return await response.json();
  } finally {inflight--;}
}, batch => {
  seen.push(...batch.events);
  if (seen.some(e => e.state === 'completed')) {
    feed.stop(); clearTimeout(timeout);
    console.log(JSON.stringify({seen, failures, maximum}));
  }
}, () => {failures++;});
feed.start();
"""

    def finish():
        engine.events.publish("browser", "speech", "running", "delta", text="Hello!")
        engine.events.publish("browser", "speech", "completed")

    timer = threading.Timer(1.5, finish)
    timer.start()
    try:
        result = subprocess.run(
            [node, "-e", script, str(feed)],
            input=json.dumps(
                {
                    "url": s.services.assistant.url,
                    "authorization": engine.auth.headers("assistant")["Authorization"],
                }
            ),
            text=True,
            capture_output=True,
            timeout=12,
            check=True,
        )
        report = json.loads(result.stdout)
        assert report["failures"] == 1
        assert report["maximum"] == 1
        assert [e["seq"] for e in report["seen"]] == [1, 2, 3, 4]
        assert report["seen"][0]["data"]["text"] == "Hello, how are you?"
    finally:
        timer.cancel()
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
        engine.close()

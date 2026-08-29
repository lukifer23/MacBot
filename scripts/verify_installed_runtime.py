"""Exercise an installed wheel with isolated state and provisioned real models.

Run from outside the checkout, under an OS network policy allowing loopback only.
No microphone, speaker or desktop tool is opened. Missing prerequisites fail.
"""

import argparse
import errno
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

import macbot
from macbot.auth import AuthStore
from macbot.config import Settings, prepare, save


def free_port(allocated):
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in allocated:
            allocated.add(port)
            return port


def verify(provisioned, model, report):
    if report.exists():
        raise FileExistsError("Choose a new report path to preserve previous verification evidence")
    assert Path(macbot.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "Use a wheel installed in a separate environment, not the editable checkout"
    )
    with socket.socket() as sock:
        sock.settimeout(2)
        assert sock.connect_ex(("1.1.1.1", 443)) in {errno.EPERM, errno.EACCES}, (
            "External network must be denied by the OS, not merely unavailable"
        )
    with tempfile.TemporaryDirectory(prefix="macbot-wheel-runtime-") as temporary:
        s = Settings(data_dir=Path(temporary) / "state")
        # This verifier explicitly exercises the optional authenticated browser
        # adapter. Normal native installs keep it disabled.
        s.services.browser_fallback_enabled = True
        s.models.llm = model
        s.models.temperature = 0
        s.models.max_tokens = 128
        allocated = set()
        s.models.llm_url = f"http://127.0.0.1:{free_port(allocated)}"
        for endpoint in [
            s.services.assistant,
            s.services.rag,
            s.services.dashboard,
            s.services.orchestrator,
        ]:
            endpoint.port = free_port(allocated)
        prepare(s)
        for name in [model, "parakeet", "amy", "silero", "minilm"]:
            source = provisioned / "models" / name
            assert source.is_dir(), f"Provision {name} first"
            (s.data_dir / "models" / name).symlink_to(source, target_is_directory=True)
        (s.data_dir / "bin").symlink_to(provisioned / "bin", target_is_directory=True)
        save(s)
        env = {key: value for key, value in os.environ.items() if not key.startswith("MACBOT")}
        env.update(MACBOT_DATA_DIR=str(s.data_dir), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        auth = AuthStore(s.data_dir)
        proc = None
        results = {
            "installed_module": str(macbot.__file__),
            "model": model,
            "external_network": "denied_by_os",
        }
        started = time.monotonic()
        try:
            with (
                (Path(temporary) / "supervisor.log").open("wb") as log,
                httpx.Client(timeout=25, trust_env=False) as client,
            ):
                proc = subprocess.Popen(
                    [sys.executable, "-m", "macbot.cli", "start"],
                    cwd=temporary,
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    assert proc.poll() is None, "Installed supervisor exited during startup"
                    try:
                        response = client.get(
                            s.services.orchestrator.url + "/ready",
                            headers=auth.headers("orchestrator"),
                        )
                        if response.status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("Installed services did not become ready")
                results["startup_seconds"] = time.monotonic() - started
                url = s.services.dashboard.url
                assert client.get(url + "/api/status").status_code == 401
                login = client.post(
                    url + "/auth/exchange",
                    json={"token": auth.issue_login()},
                    headers={"Origin": url},
                )
                login.raise_for_status()
                headers = {"Origin": url, "X-CSRF-Token": login.json()["csrf"]}
                assert "context-metrics" in client.get(url + "/").text
                assert client.get(url + "/static/dashboard.js").status_code == 200
                turn = client.post(
                    url + "/api/chat",
                    headers=headers,
                    json={
                        "message": "What is two plus two? Answer only with the number.",
                        "speak": False,
                    },
                )
                assert turn.status_code == 202, turn.text
                turn_id = turn.json()["turn_id"]
                cursor = 0
                events = []
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    batch = client.get(url + "/api/events", params={"after": cursor})
                    batch.raise_for_status()
                    data = batch.json()
                    cursor = data["cursor"]
                    events.extend(e for e in data["events"] if e["turn_id"] == turn_id)
                    if any(e["state"] in {"completed", "failed"} for e in events):
                        break
                assert events and events[-1]["state"] == "completed", events
                assert "4" in "".join(e["data"]["text"] for e in events if e["kind"] == "delta")
                status = client.get(url + "/api/status").json()
                assert status["context"]["prompt_tokens"] > 0
                assert not status["listening"]
                results["streamed_text_and_context"] = "passed"
                uploaded = client.post(
                    url + "/api/upload-documents",
                    headers=headers,
                    files={
                        "files": (
                            "verification.txt",
                            b"The verification word is cobalt.",
                            "text/plain",
                        )
                    },
                )
                uploaded.raise_for_status()
                found = client.post(
                    url + "/api/search", headers=headers, json={"query": "verification word"}
                )
                found.raise_for_status()
                assert "cobalt" in found.text
                results["document_import_and_retrieval"] = "passed"
                results["scope"] = (
                    "Offline installed-wheel software flow; no acoustic or listening acceptance"
                )
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=60)
            auth.close()
        report.write_text(json.dumps(results, indent=2))
        print(json.dumps(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisioned", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5-2b-official")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    verify(args.provisioned.resolve(), args.model, args.report.resolve())

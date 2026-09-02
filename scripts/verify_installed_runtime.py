"""Exercise an installed wheel with isolated state and provisioned real models.

Run from outside the checkout, under an OS network policy allowing loopback only.
No microphone, speaker or desktop tool is opened. Missing prerequisites fail.
"""

import argparse
import base64
import errno
import hashlib
import json
import os
import secrets
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
from macbot.native_ipc import read_frame, write_frame
from macbot.provision import voice_model


def free_port(allocated):
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in allocated:
            allocated.add(port)
            return port


def native_control_path(data_dir):
    path = data_dir / "run/control.sock"
    if len(os.fsencode(path)) < 100:
        return path
    digest = hashlib.sha256(str(data_dir).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"macbot-{os.getuid()}-{digest}/control.sock"


def connect_native(path, token):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(25)
    client.connect(str(path))
    write_frame(client, {"op": "authenticate", "token": token, "protocol_version": 3})
    hello = read_frame(client)
    assert hello == {"ok": True, "protocol": 3, "epoch": hello["epoch"]}, hello
    return client


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
        for name in [model, "parakeet", voice_model(s.models.tts_voice), "silero", "minilm"]:
            source = provisioned / "models" / name
            assert source.is_dir(), f"Provision {name} first"
            (s.data_dir / "models" / name).symlink_to(source, target_is_directory=True)
        (s.data_dir / "bin").symlink_to(provisioned / "bin", target_is_directory=True)
        save(s)
        token = secrets.token_hex(32)
        token_path = s.data_dir / "run/native-token"
        token_path.write_text(token)
        os.chmod(token_path, 0o600)
        env = {key: value for key, value in os.environ.items() if not key.startswith("MACBOT")}
        env.update(MACBOT_DATA_DIR=str(s.data_dir), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        env["MACBOT_HISTORY_KEY_FD"] = "0"
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
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                assert proc.stdin is not None
                proc.stdin.write(os.urandom(32))
                proc.stdin.close()
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
                control_path = native_control_path(s.data_dir)
                deadline = time.monotonic() + 20
                while not control_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert control_path.exists(), "Native IPC socket was not created"
                with (
                    connect_native(control_path, token) as command,
                    connect_native(control_path, token) as event_client,
                ):
                    write_frame(command, {"op": "sync", "protocol_version": 3})
                    initial = read_frame(command)
                    assert initial["ok"] and initial["protocol_version"] == 3
                    write_frame(
                        command,
                        {
                            "op": "chat",
                            "protocol_version": 3,
                            "message": "What is two plus two? Answer only with the number.",
                            "speak": False,
                        },
                    )
                    accepted = read_frame(command)
                    assert accepted["ok"] and accepted["state"] == "accepted", accepted
                    turn_id = accepted["turn_id"]
                    cursor = initial["cursor"]
                    events = []
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        write_frame(
                            event_client,
                            {
                                "op": "events",
                                "protocol_version": 3,
                                "after": cursor,
                                "epoch": initial["epoch"],
                            },
                        )
                        batch = read_frame(event_client)
                        assert batch["ok"], batch
                        cursor = batch["cursor"]
                        events.extend(e for e in batch["events"] if e["turn_id"] == turn_id)
                        if any(e["state"] in {"completed", "failed"} for e in events):
                            break
                    assert events and events[-1]["state"] == "completed", events
                    assert "4" in "".join(e["data"]["text"] for e in events if e["kind"] == "delta")
                    write_frame(command, {"op": "status", "protocol_version": 3})
                    status = read_frame(command)
                    assert status["ok"] and status["status"]["context"]["prompt_tokens"] > 0
                    assert not status["status"]["listening"]
                    results["native_streamed_text_and_context"] = "passed"
                    write_frame(
                        command,
                        {
                            "op": "document_import",
                            "protocol_version": 3,
                            "name": "verification.txt",
                            "suffix": ".txt",
                            "content": base64.b64encode(
                                b"The verification word is cobalt."
                            ).decode(),
                        },
                    )
                    uploaded = read_frame(command)
                    assert uploaded["ok"], uploaded
                    write_frame(
                        command,
                        {
                            "op": "document_search",
                            "protocol_version": 3,
                            "query": "verification word",
                        },
                    )
                    found = read_frame(command)
                    assert found["ok"] and "cobalt" in json.dumps(found)
                    results["native_document_import_and_retrieval"] = "passed"
                    write_frame(command, {"op": "sync", "protocol_version": 3})
                    reconciled = read_frame(command)
                    assert reconciled["ok"] and any(
                        message["turn_id"] == turn_id for message in reconciled["messages"]
                    )
                    results["native_reconciliation"] = "passed"
                history_files = [
                    s.data_dir / "history.sqlite3",
                    s.data_dir / "history.sqlite3-wal",
                    s.data_dir / "history.sqlite3-shm",
                ]
                plaintext = b"What is two plus two?"
                assert all(
                    not path.exists() or plaintext not in path.read_bytes()
                    for path in history_files
                ), "Conversation plaintext was present in the encrypted history store"
                results["encrypted_history_plaintext_scan"] = "passed"
                results["scope"] = "Offline installed native-IPC flow; no acoustic acceptance"
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

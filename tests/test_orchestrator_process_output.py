"""Real process ownership and output handling; no substituted Popen or HTTP clients."""

import socket
import sys
import threading
import time
import traceback

import pytest

from macbot.config import Settings
from macbot.orchestrator import MacBotOrchestrator, ServiceDefinition


def _lifecycle_diagnostic(
    supervisor: MacBotOrchestrator, service: ServiceDefinition, thread: threading.Thread
) -> str:
    process = supervisor.processes.get(service.name)
    log_path = supervisor.settings.data_dir / "logs" / f"{service.name}.log"
    stack = ""
    if thread.ident is not None and (frame := sys._current_frames().get(thread.ident)) is not None:
        stack = "".join(traceback.format_stack(frame))
    return repr(
        {
            "lifecycle": supervisor.lifecycle.get(service.name),
            "process_pid": process.pid if process else None,
            "process_poll": process.poll() if process else None,
            "log": log_path.read_text(errors="replace") if log_path.exists() else "missing",
            "thread_stack": stack,
        }
    )


def _http_service_script(port: int, *, ready_delay: float = 0, term_delay: float = 0) -> str:
    return f"""
import http.server
import json
import os
import signal
import time

time.sleep({ready_delay!r})

def terminate(*_):
    time.sleep({term_delay!r})
    raise SystemExit(0)

signal.signal(signal.SIGTERM, terminate)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"pid": os.getpid(), "status": "ready"}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_):
        pass

http.server.HTTPServer(("127.0.0.1", {port}), Handler).serve_forever()
"""


def test_service_output_is_drained(tmp_path):
    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    script = 'import sys; sys.stdout.buffer.write(b"x"*(2*1024*1024)); sys.stderr.buffer.write(b"y"*(512*1024))'
    service = ServiceDefinition("output-writer", [sys.executable, "-u", "-c", script])
    try:
        result = supervisor.start_service(service, retries=1)
        assert result["success"]
        process = supervisor.processes[service.name]
        process.wait(timeout=5)
        assert process.returncode == 0
        assert (tmp_path / "logs/output-writer.log").stat().st_size == 2560 * 1024
    finally:
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()


def test_occupied_port_never_terminates_its_owner(tmp_path):
    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        service = ServiceDefinition(
            "occupied", [sys.executable, "-c", "raise SystemExit(42)"], port=port
        )
        try:
            result = supervisor.start_service(service)
            assert not result["success"]
            assert supervisor.processes == {}
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
        finally:
            supervisor.stop_all()
            supervisor.client.close()
            supervisor.auth.close()


def test_startup_failure_closes_its_child(tmp_path):
    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    service = ServiceDefinition(
        "assistant",
        [sys.executable, "-c", "raise SystemExit(7)"],
        health_endpoint="http://127.0.0.1:1/health",
    )
    try:
        result = supervisor.start_service(service, retries=2, backoff=0.05)
        assert not result["success"]
        assert not supervisor.processes
        assert not supervisor.logs
    finally:
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()


@pytest.mark.native_integration
def test_closed_stream_time_wait_does_not_block_restart(tmp_path):
    import os

    from macbot.config import save

    # Close the server end first to leave a real TCP TIME_WAIT record. There
    # is no live listener and nothing that the supervisor may terminate.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            peer, _ = listener.accept()
            with peer:
                peer.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
                client.shutdown(socket.SHUT_WR)
                assert peer.recv(1) == b""
    settings = Settings(data_dir=tmp_path)
    settings.services.dashboard.port = port
    supervisor = MacBotOrchestrator(settings)
    save(settings)
    service = ServiceDefinition(
        "dashboard",
        [sys.executable, "-m", "macbot.web_dashboard"],
        health_endpoint=settings.services.dashboard.url + "/ready",
        port=port,
        env={
            **os.environ,
            "MACBOT_DATA_DIR": str(tmp_path),
            "MACBOT_CONFIG": str(settings.config_path),
        },
    )
    try:
        assert supervisor.start_service(service)["success"]
    finally:
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()


def test_status_remains_available_when_readiness_fails(tmp_path):
    from macbot.orchestrator import create_app

    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    try:
        client = create_app(supervisor).test_client()
        headers = {"Host": "127.0.0.1:8090", **supervisor.auth.headers("orchestrator")}
        response = client.get("/status", headers=headers)
        assert response.status_code == 200
        assert response.json["ready"] is False
        assert response.json["supervisor_rss_bytes"] > 0
        assert client.get("/ready", headers=headers).status_code == 503
    finally:
        supervisor.client.close()
        supervisor.auth.close()


def test_manual_restart_and_monitor_share_one_lifecycle(tmp_path):
    import threading

    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    service = ServiceDefinition(
        "owned-worker",
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    supervisor.service_definitions[service.name] = service
    monitor = threading.Thread(target=supervisor.monitor)
    try:
        assert supervisor.start_service(service)["success"]
        pids = [supervisor.processes[service.name].pid]
        monitor.start()
        for _ in range(4):
            assert supervisor.restart_process(service.name)["success"]
            pids.append(supervisor.processes[service.name].pid)
        assert len(set(pids)) == 5
        assert supervisor.restarts.get(service.name, 0) == 0
        assert supervisor.status()["services"][service.name]["running"]
    finally:
        supervisor.stop_all()
        if monitor.ident is not None:
            monitor.join(timeout=5)
        supervisor.client.close()
        supervisor.auth.close()


def test_readiness_reports_real_startup_phase_and_timing(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    service = ServiceDefinition(
        "assistant",
        [sys.executable, "-u", "-c", _http_service_script(port, ready_delay=1.2)],
        health_endpoint=f"http://127.0.0.1:{port}/ready",
        port=port,
    )
    result = []
    starter = threading.Thread(
        target=lambda: result.append(supervisor.start_service(service, retries=100, backoff=0.05))
    )
    try:
        supervisor.service_definitions[service.name] = service
        starter.start()
        deadline = time.monotonic() + 1
        loading = None
        while time.monotonic() < deadline:
            loading = supervisor.status()["services"][service.name]
            if loading["phase"] == "loading_speech_models":
                break
            time.sleep(0.005)
        assert loading is not None
        assert loading["phase"] == "loading_speech_models"
        assert loading["running"]
        assert not loading["ready"]
        starter.join(timeout=6)
        assert not starter.is_alive(), _lifecycle_diagnostic(supervisor, service, starter)
        assert result[0]["success"]
        ready = supervisor.status()["services"][service.name]
        assert ready["phase"] == "ready"
        assert ready["startup_ms"] >= 1100
        assert ready["readiness_attempts"] >= 2
        assert ready["last_probe_ms"] is not None
    finally:
        supervisor.stop_all()
        starter.join(timeout=2)
        supervisor.client.close()
        supervisor.auth.close()


def test_status_does_not_block_while_real_child_stops(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    service = ServiceDefinition(
        "dashboard",
        [sys.executable, "-u", "-c", _http_service_script(port, term_delay=0.25)],
        health_endpoint=f"http://127.0.0.1:{port}/ready",
        port=port,
    )
    stopper = threading.Thread(target=lambda: supervisor.stop_service(service.name))
    try:
        supervisor.service_definitions[service.name] = service
        started = supervisor.start_service(service, retries=100, backoff=0.05)
        assert started["success"], _lifecycle_diagnostic(supervisor, service, stopper)
        supervisor.stopping.set()
        shutdown_requested = supervisor.status()
        assert shutdown_requested["phase"] == "stopping"
        assert shutdown_requested["shutdown_requested"]
        assert not shutdown_requested["ready"]
        stopper.start()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if supervisor.lifecycle[service.name]["phase"] == "stopping":
                break
            time.sleep(0.005)
        started = time.monotonic()
        stopping = supervisor.status()["services"][service.name]
        status_ms = (time.monotonic() - started) * 1000
        assert stopping["phase"] == "stopping"
        assert stopping["running"]
        assert status_ms < 100
        if stopper.ident is not None:
            stopper.join(timeout=2)
        assert not stopper.is_alive()
        stopped = supervisor.status()["services"][service.name]
        assert stopped["phase"] == "stopped"
        assert stopped["shutdown_ms"] >= 200
        assert not stopped["forced_kill"]
        assert stopped["exit_code"] == 0
    finally:
        supervisor.stop_all()
        if stopper.ident is not None:
            stopper.join(timeout=2)
        supervisor.client.close()
        supervisor.auth.close()


def test_readiness_deadline_includes_slow_health_request(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = f"""
import http.server
import time
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(5)
    def log_message(self, *_):
        pass
http.server.HTTPServer(("127.0.0.1", {port}), Handler).serve_forever()
"""
    supervisor = MacBotOrchestrator(Settings(data_dir=tmp_path))
    service = ServiceDefinition(
        "rag",
        [sys.executable, "-u", "-c", script],
        health_endpoint=f"http://127.0.0.1:{port}/ready",
        port=port,
    )
    try:
        supervisor.service_definitions[service.name] = service
        started = time.monotonic()
        result = supervisor.start_service(service, retries=4, backoff=0.05)
        elapsed = time.monotonic() - started
        assert not result["success"]
        assert elapsed < 1
        assert "after" in result["error"] and "probes" in result["error"]
        failed = supervisor.status()["services"][service.name]
        assert failed["phase"] == "failed"
        assert failed["readiness_attempts"] >= 1
    finally:
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()

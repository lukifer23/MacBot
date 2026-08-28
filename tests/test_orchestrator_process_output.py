"""Real process ownership and output handling; no substituted Popen or HTTP clients."""

import socket
import sys

from macbot.config import Settings
from macbot.orchestrator import MacBotOrchestrator, ServiceDefinition


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
    import os
    import threading

    from macbot.config import save

    settings = Settings(data_dir=tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        settings.services.dashboard.port = probe.getsockname()[1]
    supervisor = MacBotOrchestrator(settings)
    save(settings)
    service = ServiceDefinition(
        "dashboard",
        [sys.executable, "-m", "macbot.web_dashboard"],
        health_endpoint=settings.services.dashboard.url + "/ready",
        port=settings.services.dashboard.port,
        env={
            **os.environ,
            "MACBOT_DATA_DIR": str(tmp_path),
            "MACBOT_CONFIG": str(settings.config_path),
        },
    )
    supervisor.service_definitions["dashboard"] = service
    monitor = threading.Thread(target=supervisor.monitor)
    try:
        assert supervisor.start_service(service)["success"]
        monitor.start()
        pids = [supervisor.processes["dashboard"].pid]
        for _ in range(4):
            assert supervisor.restart_process("dashboard")["success"]
            pids.append(supervisor.processes["dashboard"].pid)
        assert len(set(pids)) == 5
        assert supervisor.restarts.get("dashboard", 0) == 0
        assert supervisor.status()["services"]["dashboard"]["ready"]
    finally:
        supervisor.stop_all()
        if monitor.ident is not None:
            monitor.join(timeout=5)
        supervisor.client.close()
        supervisor.auth.close()

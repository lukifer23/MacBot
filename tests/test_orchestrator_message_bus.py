import socket
import time

from macbot import config as CFG
from macbot.orchestrator import MacBotOrchestrator


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(condition, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def test_orchestrator_message_bus_startup(monkeypatch):
    host = "127.0.0.1"
    port = _get_free_port()

    original_get = CFG.get

    def fake_get(path: str, default=None):
        if path == "communication.message_bus.host":
            return host
        if path == "communication.message_bus.port":
            return port
        return original_get(path, default)

    monkeypatch.setattr(CFG, "get", fake_get)

    orchestrator = MacBotOrchestrator()
    orchestrator.config.setdefault("communication", {}).setdefault("message_bus", {})
    orchestrator.config["communication"]["message_bus"].update({"host": host, "port": port})
    orchestrator.service_definitions = {}

    monkeypatch.setattr(orchestrator, "start_llama_server", lambda: True)
    monkeypatch.setattr(orchestrator, "start_control_server", lambda: None)
    monkeypatch.setattr(orchestrator.health_monitor, "start_monitoring", lambda: None)
    monkeypatch.setattr(orchestrator.health_monitor, "stop_monitoring", lambda: None)

    try:
        assert orchestrator.start_all()
        assert orchestrator.bus_client is not None
        assert _wait_for(orchestrator.bus_client.is_connected)

        orchestrator.bus_client.send_message(
            {
                "type": "service_registered",
                "service_id": "svc-test",
                "capabilities": [],
            }
        )

        assert _wait_for(lambda: "svc-test" in orchestrator.service_status)
        status = orchestrator.service_status["svc-test"]
        assert status["status"] == "registered"
        assert status["type"] == "orchestrator"
    finally:
        orchestrator.stop_all()
        assert orchestrator.bus_client is None
        assert orchestrator.message_bus is None

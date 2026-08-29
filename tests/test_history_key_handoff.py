import os
import sys

from macbot.config import Settings
from macbot.orchestrator import MacBotOrchestrator, ServiceDefinition


def test_supervisor_reissues_history_key_pipe_for_every_assistant_launch(tmp_path):
    settings = Settings(data_dir=tmp_path, privacy={"history_enabled": False})
    supervisor = MacBotOrchestrator(settings, history_key=b"h" * 32)
    command = [
        sys.executable,
        "-c",
        (
            "from macbot.history import runtime_history_key; import sys; "
            "sys.exit(0 if runtime_history_key() == b'h' * 32 else 9)"
        ),
    ]
    service = ServiceDefinition("assistant", command, env=dict(os.environ))
    supervisor.service_definitions[service.name] = service
    try:
        for _ in range(2):
            result = supervisor.start_service(service, retries=10, backoff=0.01)
            assert result["success"] is True
            assert supervisor.processes["assistant"].wait(timeout=5) == 0
    finally:
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()

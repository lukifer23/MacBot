import os
import sys
from subprocess import TimeoutExpired

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.orchestrator import MacBotOrchestrator, ServiceDefinition


def test_service_output_is_drained():
    orchestrator = MacBotOrchestrator()
    orchestrator.service_definitions = {}

    script = (
        "import sys\n"
        "data = b'x' * (2 * 1024 * 1024)\n"
        "sys.stdout.buffer.write(data)\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stderr.buffer.write(b'y' * (512 * 1024))\n"
        "sys.stderr.buffer.flush()\n"
    )

    noisy_service = ServiceDefinition(
        name="dummy_writer",
        command=[sys.executable, "-u", "-c", script],
    )

    try:
        result = orchestrator.start_service(noisy_service, retries=1)
        assert result["success"], result
        proc = orchestrator.processes["dummy_writer"]
        try:
            proc.wait(timeout=5)
        except TimeoutExpired as exc:
            pytest.fail(f"process output was not drained: {exc}")
        assert proc.returncode == 0
    finally:
        orchestrator.stop_all()

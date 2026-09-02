import pytest

from macbot.task_protocol import CONTRACT, TASK_PROTOCOL_VERSION, legal_commands
from macbot.tasks import FailureClass, StepState, TaskState, require_task_transition


def test_packaged_task_protocol_has_exact_python_coverage():
    assert CONTRACT["protocol_version"] == TASK_PROTOCOL_VERSION == 3
    assert set(CONTRACT["task_states"]) == {item.value for item in TaskState}
    assert set(CONTRACT["step_states"]) == {item.value for item in StepState}
    assert set(CONTRACT["failure_classes"]) == {item.value for item in FailureClass}


def test_every_advertised_cancel_command_has_a_legal_backend_transition():
    direct_cancel = {
        TaskState.PROPOSED,
        TaskState.AWAITING_AUTHORIZATION,
        TaskState.QUEUED,
        TaskState.BLOCKED,
    }
    requested_cancel = {
        TaskState.RUNNING,
        TaskState.PAUSE_REQUESTED,
        TaskState.PAUSED,
    }
    for state in TaskState:
        commands = legal_commands(state)
        if "cancel" not in commands:
            continue
        target = TaskState.CANCELLED if state in direct_cancel else TaskState.CANCEL_REQUESTED
        assert state in direct_cancel | requested_cancel
        assert require_task_transition(state.value, target.value)[1] is target


def test_unknown_task_state_cannot_synthesize_commands():
    with pytest.raises(ValueError):
        legal_commands("waiting")

import os

import pytest

from macbot.capabilities import CapabilityAuthority, CapabilityBroker, CapabilityDefinition
from macbot.config import Settings, prepare
from macbot.history import HistoryStore
from macbot.tasks import (
    Intent,
    PlannedAction,
    StepState,
    TaskPlan,
    TaskState,
)


@pytest.fixture
def ledger(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    store = HistoryStore(settings, os.urandom(32))
    try:
        yield store
    finally:
        store.close()


def plan(session: str, capability: str, safety: str, arguments: dict) -> TaskPlan:
    action = PlannedAction(capability, arguments, capability, safety)
    return TaskPlan.from_intent(session, "turn-1", capability, Intent("act", (action,)))


def persist(ledger: HistoryStore, task: TaskPlan):
    steps = task.durable_steps()
    ledger.create_task(task.as_dict(), [step.as_dict() for step in steps])
    return steps


def test_task_and_steps_are_encrypted_and_written_atomically(ledger):
    task = plan("private-session", "lookup_secret", "read", {"query": "cobalt-marker"})
    persist(ledger, task)

    restored = ledger.load_task(task.task_id)
    restored_steps = ledger.load_steps(task.task_id)
    assert restored["task_id"] == task.task_id
    assert restored_steps[0]["arguments"] == {"query": "cobalt-marker"}
    assert b"cobalt-marker" not in ledger.path.read_bytes()

    duplicate_task = plan("private-session", "lookup_secret", "read", {"query": "other"})
    first = duplicate_task.durable_steps()[0].as_dict()
    second = {**first, "step_id": "another-step", "ordinal": 1}
    with pytest.raises(ValueError, match="unique idempotency"):
        ledger.create_task(duplicate_task.as_dict(), [first, second])
    assert ledger.load_task(duplicate_task.task_id) is None
    assert len(ledger.load_steps(task.task_id)) == 1


def test_transitions_are_write_ahead_and_terminal_states_cannot_reopen(ledger):
    task = plan("s", "clock", "read", {})
    step = persist(ledger, task)[0]

    ledger.transition_task(task.task_id, TaskState.QUEUED)
    assert ledger.transition_task(task.task_id, TaskState.RUNNING)["state"] == "running"
    ledger.transition_step(step.step_id, StepState.AUTHORIZED)
    running = ledger.transition_step(step.step_id, StepState.RUNNING)
    assert running["state"] == "running" and running["started_ns"]
    completed = ledger.transition_step(step.step_id, StepState.SUCCEEDED, result={"time": "12:00"})
    assert completed["result"] == {"time": "12:00"}
    assert ledger.transition_task(task.task_id, TaskState.COMPLETED)["state"] == "completed"
    with pytest.raises(ValueError, match="Invalid step transition"):
        ledger.transition_step(step.step_id, StepState.RUNNING)


def test_recovery_quarantines_running_steps_and_classifies_side_effects(ledger):
    read_task = plan("s", "clock", "read", {})
    side_task = plan("s", "open_app", "requested_side_effect", {"app": "Notes"})
    read_step = persist(ledger, read_task)[0]
    side_step = persist(ledger, side_task)[0]
    for task, step in ((read_task, read_step), (side_task, side_step)):
        ledger.transition_task(task.task_id, TaskState.QUEUED)
        ledger.transition_task(task.task_id, TaskState.RUNNING)
        ledger.transition_step(step.step_id, StepState.AUTHORIZED)
        ledger.transition_step(step.step_id, StepState.RUNNING)

    recovered = {item["step_id"]: item["disposition"] for item in ledger.recover_inflight_steps()}
    assert recovered == {
        read_step.step_id: "retry_safe",
        side_step.step_id: "verify_before_retry",
    }
    assert ledger.load_steps(read_task.task_id)[0]["state"] == "blocked"
    assert ledger.load_steps(side_task.task_id)[0]["state"] == "unknown_effect"
    assert ledger.load_task(read_task.task_id)["state"] == "blocked"
    assert ledger.requeue_recovered_read_step(read_step.step_id)["state"] == "planned"
    with pytest.raises(PermissionError, match="retry-safe"):
        ledger.requeue_recovered_read_step(side_step.step_id)
    assert ledger.recover_inflight_steps() == []


def test_broker_consumes_one_receipt_and_persists_the_real_result(ledger):
    task = plan("s", "uppercase", "read", {"text": "hello"})
    step = persist(ledger, task)[0]
    calls = []

    def uppercase(arguments):
        calls.append(arguments["text"])
        return {"text": arguments["text"].upper()}

    broker = CapabilityBroker(
        ledger,
        [
            CapabilityDefinition(
                "uppercase", "Uppercase bounded text", {"text": "string"}, uppercase
            )
        ],
    )
    assert broker.manifest == (
        {
            "name": "uppercase",
            "description": "Uppercase bounded text",
            "arguments": {"text": "string"},
        },
    )
    receipt = broker.issue(step, CapabilityAuthority.READ)
    assert receipt.split(".", 1)[1].encode() not in ledger.path.read_bytes()
    assert broker.execute(step, receipt) == {"text": "HELLO"}
    assert calls == ["hello"]
    assert ledger.load_steps(task.task_id)[0]["result"] == {"text": "HELLO"}
    with pytest.raises(PermissionError, match="consumed"):
        broker.execute(step, receipt)
    assert calls == ["hello"]


def test_broker_rejects_wrong_authority_and_persists_executor_failure(ledger):
    task = plan("s", "fail_action", "requested_side_effect", {"target": "Notes"})
    step = persist(ledger, task)[0]

    def fail_action(arguments):
        raise RuntimeError("operator-visible failure")

    broker = CapabilityBroker(
        ledger,
        [
            CapabilityDefinition(
                "fail_action", "Exercise durable failure", {"target": "string"}, fail_action
            )
        ],
    )
    with pytest.raises(PermissionError, match="authority"):
        broker.issue(step, CapabilityAuthority.READ)
    receipt = broker.issue(step, CapabilityAuthority.EXPLICIT_REQUEST)
    with pytest.raises(RuntimeError, match="operator-visible"):
        broker.execute(step, receipt)
    failed = ledger.load_steps(task.task_id)[0]
    assert failed["state"] == "unknown_effect"
    assert failed["error"] == "RuntimeError: operator-visible failure"
    with pytest.raises(PermissionError, match="not eligible"):
        broker.issue(step, CapabilityAuthority.EXPLICIT_REQUEST)

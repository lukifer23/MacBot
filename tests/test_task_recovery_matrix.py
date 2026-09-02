import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from macbot.capabilities import (
    CapabilityAuthority,
    CapabilityBroker,
    CapabilityDefinition,
    RequestContext,
)
from macbot.config import Settings, prepare
from macbot.history import HistoryStore
from macbot.tasks import Intent, PlannedAction, StepState, TaskPlan, TaskState


@pytest.fixture
def ledger(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    store = HistoryStore(settings, os.urandom(32))
    try:
        yield store
    finally:
        store.close()


def persisted_step(
    ledger: HistoryStore,
    capability: str,
    safety: str = "read",
    arguments: dict | None = None,
):
    arguments = arguments or {}
    action = PlannedAction(capability, arguments, capability, safety)
    task = TaskPlan.from_intent("recovery", "turn", capability, Intent("act", (action,)))
    step = task.durable_steps()[0]
    ledger.create_task(task.as_dict(), [step.as_dict()])
    return task, step


def queued_running(ledger: HistoryStore, task: TaskPlan) -> None:
    ledger.transition_task(task.task_id, TaskState.QUEUED)
    ledger.transition_task(task.task_id, TaskState.RUNNING)


def context(task: TaskPlan, step) -> RequestContext:
    return RequestContext(
        request_id=task.task_id,
        task_id=task.task_id,
        step_id=step.step_id,
        attempt_id=uuid.uuid4().hex,
        deadline_ns=time.time_ns() + 1_000_000_000,
        cancellation=threading.Event(),
        authorization_version=0,
    )


def test_recovery_matrix_distinguishes_never_started_read_and_side_effect(ledger):
    queued_task, queued_step = persisted_step(ledger, "queued_read")
    ledger.transition_task(queued_task.task_id, TaskState.QUEUED)

    read_task, read_step = persisted_step(ledger, "running_read")
    side_task, side_step = persisted_step(ledger, "running_side_effect", "requested_side_effect")
    for task, step in ((read_task, read_step), (side_task, side_step)):
        queued_running(ledger, task)
        ledger.transition_step(step.step_id, StepState.AUTHORIZED)
        ledger.transition_step(step.step_id, StepState.RUNNING)

    recovered = {item["step_id"]: item["disposition"] for item in ledger.recover_inflight_steps()}

    assert queued_step.step_id not in recovered
    assert ledger.load_task(queued_task.task_id)["state"] == "blocked"
    assert ledger.load_task(queued_task.task_id)["recovery_reason"] == "execution_interrupted"
    assert ledger.load_steps(queued_task.task_id)[0]["state"] == "planned"
    assert recovered[read_step.step_id] == "retry_safe"
    assert ledger.load_steps(read_task.task_id)[0]["state"] == "blocked"
    assert recovered[side_step.step_id] == "verify_before_retry"
    assert ledger.load_steps(side_task.task_id)[0]["state"] == "unknown_effect"


def test_receipt_claim_and_step_start_recover_as_one_atomic_boundary(ledger):
    task, step = persisted_step(ledger, "atomic_read")
    queued_running(ledger, task)
    broker = CapabilityBroker(
        ledger,
        [CapabilityDefinition("atomic_read", "Read atomically", {}, lambda _: {"ok": True})],
    )
    receipt = broker.issue(step, CapabilityAuthority.READ)
    receipt_id = receipt.split(".", 1)[0]
    arguments_hash = broker.arguments_hash("atomic_read", {})

    ledger.consume_receipt_and_start_step(receipt, step.step_id, "atomic_read", arguments_hash)
    consumed = ledger.db.execute(
        "SELECT consumed FROM capability_receipts WHERE id=?", (receipt_id,)
    ).fetchone()[0]
    assert consumed == 1
    assert ledger.load_steps(task.task_id)[0]["state"] == "running"
    with pytest.raises(PermissionError, match="consumed"):
        ledger.consume_receipt_and_start_step(receipt, step.step_id, "atomic_read", arguments_hash)

    recovered = ledger.recover_inflight_steps()
    durable_step = ledger.load_steps(task.task_id)[0]
    assert recovered == [
        {"task_id": task.task_id, "step_id": step.step_id, "disposition": "retry_safe"}
    ]
    assert durable_step["state"] == "blocked"
    assert durable_step["recovery_disposition"] == "retry_safe"


def test_concurrent_double_consume_executes_capability_exactly_once(ledger):
    task, step = persisted_step(ledger, "count_once")
    queued_running(ledger, task)
    count = 0
    count_lock = threading.Lock()

    def execute_once(_):
        nonlocal count
        with count_lock:
            count += 1
        return {"count": count}

    broker = CapabilityBroker(
        ledger,
        [CapabilityDefinition("count_once", "Count one execution", {}, execute_once)],
    )
    receipt = broker.issue(step, CapabilityAuthority.READ)

    def attempt():
        try:
            return ("ok", broker.execute(step, receipt, context(task, step)))
        except PermissionError as exc:
            return ("denied", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))

    assert sorted(item[0] for item in outcomes) == ["denied", "ok"]
    assert count == 1
    assert ledger.load_steps(task.task_id)[0]["state"] == "succeeded"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (TaskState.PAUSE_REQUESTED, TaskState.PAUSED),
        (TaskState.CANCEL_REQUESTED, TaskState.CANCELLED),
    ],
)
def test_restart_finishes_requested_control_transitions(ledger, before, after):
    task, _ = persisted_step(ledger, "control_read")
    queued_running(ledger, task)
    ledger.transition_task(task.task_id, before)

    ledger.recover_inflight_steps()

    assert ledger.load_task(task.task_id)["state"] == after.value


def test_restart_revokes_unconsumed_authorization_and_requeues_step(ledger):
    task, step = persisted_step(ledger, "authorized_read")
    queued_running(ledger, task)
    broker = CapabilityBroker(
        ledger,
        [CapabilityDefinition("authorized_read", "Read after restart", {}, lambda _: {"ok": True})],
    )
    receipt = broker.issue(step, CapabilityAuthority.READ)
    assert ledger.load_steps(task.task_id)[0]["state"] == "authorized"

    ledger.recover_inflight_steps()

    recovered_step = ledger.load_steps(task.task_id)[0]
    assert recovered_step["state"] == "blocked"
    assert recovered_step["recovery_disposition"] == "not_started"
    with pytest.raises(PermissionError, match="expired|consumed|mismatched"):
        broker.execute(step, receipt, context(task, step))


def test_task_revision_rejects_stale_state_commit(ledger):
    task, _ = persisted_step(ledger, "revision_read")
    queued = ledger.transition_task(task.task_id, TaskState.QUEUED)
    running = ledger.transition_task(
        task.task_id,
        TaskState.RUNNING,
        expected_revision=queued["revision"],
    )
    ledger.transition_task(
        task.task_id,
        TaskState.CANCEL_REQUESTED,
        expected_revision=running["revision"],
    )
    with pytest.raises(RuntimeError, match="changed concurrently"):
        ledger.transition_task(
            task.task_id,
            TaskState.COMPLETED,
            expected_revision=running["revision"],
        )


def test_side_effect_that_crashes_after_effect_is_never_replayed(ledger, tmp_path):
    marker = tmp_path / "side-effect-marker"
    task, step = persisted_step(ledger, "write_marker", "requested_side_effect", {"value": "done"})
    queued_running(ledger, task)

    def write_then_crash(arguments):
        marker.write_text(arguments["value"])
        raise SystemExit("simulated process crash")

    broker = CapabilityBroker(
        ledger,
        [
            CapabilityDefinition(
                "write_marker", "Write one durable marker", {"value": "string"}, write_then_crash
            )
        ],
    )
    receipt = broker.issue(step, CapabilityAuthority.EXPLICIT_REQUEST)
    with pytest.raises(SystemExit, match="simulated process crash"):
        broker.execute(step, receipt, context(task, step))
    assert marker.read_text() == "done"

    recovered = ledger.recover_inflight_steps()
    assert recovered[0]["disposition"] == "verify_before_retry"
    assert ledger.load_steps(task.task_id)[0]["state"] == "unknown_effect"
    with pytest.raises(PermissionError, match="not eligible"):
        broker.issue(step, CapabilityAuthority.EXPLICIT_REQUEST)
    assert marker.read_text() == "done"

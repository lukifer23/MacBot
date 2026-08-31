"""Typed task records shared by planning, execution, persistence, and UI adapters."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

PlanMode = Literal["respond", "clarify", "act"]
ActionState = Literal["accepted", "running", "completed", "denied", "failed", "interrupted"]
SafetyClass = Literal["read", "requested_side_effect", "restricted"]


class TaskState(StrEnum):
    PROPOSED = "proposed"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepState(StrEnum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    UNKNOWN_EFFECT = "unknown_effect"


class RecoveryDisposition(StrEnum):
    RETRY_SAFE = "retry_safe"
    VERIFY_BEFORE_RETRY = "verify_before_retry"


TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PROPOSED: frozenset(
        {TaskState.AWAITING_AUTHORIZATION, TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.AWAITING_AUTHORIZATION: frozenset(
        {TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.QUEUED: frozenset(
        {TaskState.RUNNING, TaskState.BLOCKED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.AWAITING_AUTHORIZATION,
            TaskState.PAUSE_REQUESTED,
            TaskState.CANCEL_REQUESTED,
            TaskState.BLOCKED,
            TaskState.COMPLETED,
            TaskState.PARTIAL,
            TaskState.FAILED,
        }
    ),
    TaskState.PAUSE_REQUESTED: frozenset({TaskState.PAUSED, TaskState.PARTIAL, TaskState.FAILED}),
    TaskState.PAUSED: frozenset(
        {TaskState.QUEUED, TaskState.CANCEL_REQUESTED, TaskState.BLOCKED, TaskState.FAILED}
    ),
    TaskState.CANCEL_REQUESTED: frozenset({TaskState.CANCELLED, TaskState.PARTIAL}),
    TaskState.BLOCKED: frozenset({TaskState.QUEUED, TaskState.PARTIAL, TaskState.FAILED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.PARTIAL: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.PLANNED: frozenset({StepState.AUTHORIZED, StepState.BLOCKED, StepState.SKIPPED}),
    StepState.AUTHORIZED: frozenset({StepState.RUNNING, StepState.BLOCKED, StepState.SKIPPED}),
    StepState.RUNNING: frozenset(
        {
            StepState.SUCCEEDED,
            StepState.FAILED,
            StepState.BLOCKED,
            StepState.UNKNOWN_EFFECT,
        }
    ),
    StepState.SUCCEEDED: frozenset(),
    StepState.FAILED: frozenset(),
    StepState.BLOCKED: frozenset({StepState.AUTHORIZED, StepState.SKIPPED}),
    StepState.SKIPPED: frozenset(),
    StepState.UNKNOWN_EFFECT: frozenset(),
}


def require_task_transition(current: str, target: str) -> tuple[TaskState, TaskState]:
    before, after = TaskState(current), TaskState(target)
    if after not in TASK_TRANSITIONS[before]:
        raise ValueError(f"Invalid task transition: {before.value} -> {after.value}")
    return before, after


def require_step_transition(current: str, target: str) -> tuple[StepState, StepState]:
    before, after = StepState(current), StepState(target)
    if after not in STEP_TRANSITIONS[before]:
        raise ValueError(f"Invalid step transition: {before.value} -> {after.value}")
    return before, after


def recovery_disposition(safety_class: SafetyClass) -> RecoveryDisposition:
    return (
        RecoveryDisposition.RETRY_SAFE
        if safety_class == "read"
        else RecoveryDisposition.VERIFY_BEFORE_RETRY
    )


@dataclass(frozen=True)
class PlannedAction:
    name: str
    arguments: dict[str, Any]
    source_span: str
    safety_class: SafetyClass

    @property
    def idempotency_key(self) -> str:
        canonical = json.dumps(
            {"name": self.name, "arguments": self.arguments}, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Intent:
    mode: PlanMode
    actions: tuple[PlannedAction, ...] = ()
    clarification: str = ""


@dataclass
class ActionResult:
    action_id: str
    name: str
    arguments: dict[str, Any]
    source_span: str
    safety_class: SafetyClass
    idempotency_key: str
    state: ActionState = "accepted"
    result: dict[str, Any] | None = None
    error: str | None = None
    started_ns: int | None = None
    completed_ns: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskStep:
    task_id: str
    session_id: str
    turn_id: str
    ordinal: int
    capability: str
    arguments: dict[str, Any]
    safety_class: SafetyClass
    idempotency_key: str
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: StepState = StepState.PLANNED
    result: dict[str, Any] | None = None
    error: str | None = None
    created_ns: int = field(default_factory=time.time_ns)
    started_ns: int | None = None
    completed_ns: int | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


@dataclass
class TaskPlan:
    session_id: str
    turn_id: str
    user_text_hash: str
    mode: PlanMode
    actions: list[ActionResult] = field(default_factory=list)
    clarification: str = ""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_ns: int = field(default_factory=time.time_ns)
    state: TaskState = TaskState.PROPOSED

    @classmethod
    def from_intent(cls, session_id: str, turn_id: str, text: str, intent: Intent) -> TaskPlan:
        return cls(
            session_id=session_id,
            turn_id=turn_id,
            user_text_hash=hashlib.sha256(text.encode()).hexdigest(),
            mode=intent.mode,
            clarification=intent.clarification,
            actions=[
                ActionResult(
                    action_id=uuid.uuid4().hex,
                    name=item.name,
                    arguments=item.arguments,
                    source_span=item.source_span,
                    safety_class=item.safety_class,
                    idempotency_key=item.idempotency_key,
                )
                for item in intent.actions
            ],
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = str(self.state)
        return value

    def durable_steps(self) -> list[TaskStep]:
        return [
            TaskStep(
                task_id=self.task_id,
                session_id=self.session_id,
                turn_id=self.turn_id,
                ordinal=index,
                capability=action.name,
                arguments=dict(action.arguments),
                safety_class=action.safety_class,
                idempotency_key=action.idempotency_key,
                step_id=action.action_id,
            )
            for index, action in enumerate(self.actions)
        ]

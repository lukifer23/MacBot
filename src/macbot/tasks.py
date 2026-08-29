"""Typed task records shared by planning, execution, persistence, and UI adapters."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PlanMode = Literal["respond", "clarify", "act"]
ActionState = Literal["accepted", "running", "completed", "denied", "failed", "interrupted"]
SafetyClass = Literal["read", "requested_side_effect", "restricted"]


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
class TaskPlan:
    session_id: str
    turn_id: str
    user_text_hash: str
    mode: PlanMode
    actions: list[ActionResult] = field(default_factory=list)
    clarification: str = ""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_ns: int = field(default_factory=time.time_ns)
    state: ActionState = "accepted"

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
        return asdict(self)

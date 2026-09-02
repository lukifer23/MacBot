"""Versioned Task protocol contract shared by persistence, IPC, and native UI."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .tasks import FailureClass, StepState, TaskState

TASK_PROTOCOL_VERSION = 3


def _load() -> dict[str, Any]:
    path = files("macbot").joinpath("defaults/task_protocol_v3.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "protocol_version",
        "channels",
        "operations",
        "reconciliation_fields",
        "error_fields",
        "task_states",
        "step_states",
        "failure_classes",
        "commands",
        "legal_commands",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("Task protocol contract has invalid fields")
    coverage = {
        "task_states": [item.value for item in TaskState],
        "step_states": [item.value for item in StepState],
        "failure_classes": [item.value for item in FailureClass],
    }
    if value["protocol_version"] != TASK_PROTOCOL_VERSION:
        raise RuntimeError("Task protocol contract version is unsupported")
    exact = {
        "channels": {"command", "event", "audio"},
        "reconciliation_fields": {
            "protocol_version",
            "epoch",
            "cursor",
            "messages",
            "tasks",
            "active_turn",
        },
        "error_fields": {"code", "message", "retryable", "failure_class"},
    }
    for field, exact_required in exact.items():
        declared = value[field]
        if not isinstance(declared, list) or set(declared) != exact_required:
            raise RuntimeError(f"Protocol {field} coverage is invalid")
    operations = value["operations"]
    if not isinstance(operations, list) or len(operations) != len(set(operations)):
        raise RuntimeError("Protocol operation coverage is invalid")
    for field, required in coverage.items():
        declared = value[field]
        if not isinstance(declared, list) or len(declared) != len(set(declared)):
            raise RuntimeError(f"Task protocol {field} contains duplicates")
        if set(declared) != set(required):
            raise RuntimeError(f"Task protocol {field} coverage is invalid")
    commands = value["commands"]
    legal = value["legal_commands"]
    if (
        not isinstance(commands, list)
        or len(commands) != len(set(commands))
        or not isinstance(legal, dict)
        or set(legal) != set(coverage["task_states"])
        or any(
            not isinstance(items, list)
            or len(items) != len(set(items))
            or not set(items).issubset(commands)
            for items in legal.values()
        )
    ):
        raise RuntimeError("Task protocol command coverage is invalid")
    return value


CONTRACT = _load()
PROTOCOL_OPERATIONS = frozenset(CONTRACT["operations"])


def legal_commands(state: TaskState | str) -> list[str]:
    return list(CONTRACT["legal_commands"][TaskState(str(state)).value])


def require_task_protocol(request: dict[str, Any]) -> None:
    if request.get("protocol_version") != TASK_PROTOCOL_VERSION:
        raise ValueError(f"Task operations require protocol_version {TASK_PROTOCOL_VERSION}")

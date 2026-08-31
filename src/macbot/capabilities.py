"""Bounded, receipt-gated execution boundary for durable task capabilities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from .tasks import StepState, TaskStep

JsonType = Literal["string", "integer", "number", "boolean", "object", "array"]


class CapabilityAuthority(StrEnum):
    READ = "read"
    EXPLICIT_REQUEST = "explicit_request"
    APPROVAL_REQUIRED = "approval_required"


class CapabilityLedger(Protocol):
    def issue_capability_receipt(
        self,
        step_id: str,
        capability: str,
        arguments_hash: str,
        authority: str,
        ttl_seconds: int,
    ) -> str: ...

    def consume_capability_receipt(
        self, receipt: str, step_id: str, capability: str, arguments_hash: str
    ) -> dict[str, Any]: ...

    def transition_step(
        self,
        step_id: str,
        target: StepState | str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CapabilityDefinition:
    name: str
    description: str
    arguments: Mapping[str, JsonType]
    executor: Callable[[dict[str, Any]], dict[str, Any]]

    def public_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": dict(self.arguments),
        }


class CapabilityBroker:
    MAX_CAPABILITIES = 32
    MAX_ARGUMENT_BYTES = 16 * 1024

    def __init__(self, ledger: CapabilityLedger, definitions: Iterable[CapabilityDefinition]):
        self.ledger = ledger
        items = list(definitions)
        if not 1 <= len(items) <= self.MAX_CAPABILITIES:
            raise ValueError("Capability manifest must contain 1–32 definitions")
        self._definitions: dict[str, CapabilityDefinition] = {}
        for item in items:
            if (
                not item.name.isidentifier()
                or not 1 <= len(item.name) <= 64
                or not item.description.strip()
                or len(item.description) > 300
                or len(item.arguments) > 16
                or any(not key.isidentifier() for key in item.arguments)
                or any(kind not in self._validators for kind in item.arguments.values())
            ):
                raise ValueError("Capability definition is invalid or unbounded")
            if item.name in self._definitions:
                raise ValueError("Capability names must be unique")
            self._definitions[item.name] = item

    @property
    def manifest(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.public_manifest() for item in self._definitions.values())

    @staticmethod
    def arguments_hash(capability: str, arguments: Mapping[str, Any]) -> str:
        try:
            canonical = json.dumps(
                {"capability": capability, "arguments": arguments},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("Capability arguments must be finite JSON values") from exc
        if len(canonical) > CapabilityBroker.MAX_ARGUMENT_BYTES:
            raise ValueError("Capability arguments exceed 16 KiB")
        return hashlib.sha256(canonical).hexdigest()

    def issue(
        self,
        step: TaskStep | Mapping[str, Any],
        authority: CapabilityAuthority,
        *,
        ttl_seconds: int = 60,
    ) -> str:
        values = self._step_values(step)
        self._validate_authority(values["safety_class"], authority)
        arguments = self._validate_arguments(values["capability"], values["arguments"])
        return self.ledger.issue_capability_receipt(
            values["step_id"],
            values["capability"],
            self.arguments_hash(values["capability"], arguments),
            authority.value,
            ttl_seconds,
        )

    def execute(self, step: TaskStep | Mapping[str, Any], receipt: str) -> dict[str, Any]:
        values = self._step_values(step)
        arguments = self._validate_arguments(values["capability"], values["arguments"])
        digest = self.arguments_hash(values["capability"], arguments)
        self.ledger.consume_capability_receipt(
            receipt, values["step_id"], values["capability"], digest
        )
        self.ledger.transition_step(values["step_id"], StepState.RUNNING)
        definition = self._definitions[values["capability"]]
        try:
            result = definition.executor(arguments)
            if not isinstance(result, dict):
                raise TypeError("Capability executor must return a JSON object")
            self.arguments_hash(values["capability"] + ":result", result)
        except Exception as exc:
            target = (
                StepState.FAILED if values["safety_class"] == "read" else StepState.UNKNOWN_EFFECT
            )
            self.ledger.transition_step(
                values["step_id"], target, error=f"{type(exc).__name__}: {exc}"
            )
            raise
        self.ledger.transition_step(values["step_id"], StepState.SUCCEEDED, result=result)
        return result

    @staticmethod
    def _step_values(step: TaskStep | Mapping[str, Any]) -> dict[str, Any]:
        values = step.as_dict() if isinstance(step, TaskStep) else dict(step)
        required = {"step_id", "capability", "arguments", "safety_class"}
        if not required.issubset(values):
            raise ValueError("Durable task step is incomplete")
        return values

    def _validate_arguments(self, capability: str, arguments: Any) -> dict[str, Any]:
        definition = self._definitions.get(capability)
        if not definition:
            raise PermissionError("Capability is not in the bounded manifest")
        if not isinstance(arguments, dict) or set(arguments) != set(definition.arguments):
            raise ValueError("Capability arguments do not match the manifest")
        for key, kind in definition.arguments.items():
            if not self._validators[kind](arguments[key]):
                raise ValueError(f"Capability argument {key} must be {kind}")
        self.arguments_hash(capability, arguments)
        return dict(arguments)

    @staticmethod
    def _validate_authority(safety_class: str, authority: CapabilityAuthority) -> None:
        allowed = {
            "read": {CapabilityAuthority.READ},
            "requested_side_effect": {
                CapabilityAuthority.EXPLICIT_REQUEST,
                CapabilityAuthority.APPROVAL_REQUIRED,
            },
            "restricted": {CapabilityAuthority.APPROVAL_REQUIRED},
        }
        if authority not in allowed.get(safety_class, set()):
            raise PermissionError("Capability authority does not satisfy the step safety class")

    _validators: dict[JsonType, Callable[[Any], bool]] = {
        "string": lambda value: (
            isinstance(value, str) and len(value) <= 2000 and "\x00" not in value
        ),
        "integer": lambda value: type(value) is int,
        "number": lambda value: type(value) in {int, float},
        "boolean": lambda value: type(value) is bool,
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
    }

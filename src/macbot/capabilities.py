"""Bounded, receipt-gated execution boundary for durable task capabilities."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from .tasks import FailureClass, StepState, TaskStep, classify_failure

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

    def consume_receipt_and_start_step(
        self, receipt: str, step_id: str, capability: str, arguments_hash: str
    ) -> dict[str, Any]: ...

    def transition_step(
        self,
        step_id: str,
        target: StepState | str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def save_evidence(self, record: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class RequestContext:
    """Identity, deadline, cancellation, and authority carried across one capability call."""

    request_id: str
    task_id: str
    step_id: str
    attempt_id: str
    deadline_ns: int
    cancellation: threading.Event
    authorization_version: int

    def check(self) -> None:
        if self.cancellation.is_set():
            raise InterruptedError("Capability request was cancelled")
        if time.time_ns() >= self.deadline_ns:
            raise TimeoutError("Capability request deadline expired")


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    task_id: str
    step_id: str
    source_kind: str
    source_id: str
    canonical_url: str | None
    title: str
    retrieved_ns: int
    excerpt: str
    body_hash: str
    relevance: float | None
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "retrieved_ns": self.retrieved_ns,
            "excerpt": self.excerpt,
            "body_hash": self.body_hash,
            "relevance": self.relevance,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class CapabilityResult:
    outcome: dict[str, Any]
    failure_class: str | None
    retryable: bool
    started_ns: int
    completed_ns: int
    provenance: dict[str, Any]
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "failure_class": self.failure_class,
            "retryable": self.retryable,
            "timing": {
                "started_ns": self.started_ns,
                "completed_ns": self.completed_ns,
                "duration_ms": (self.completed_ns - self.started_ns) / 1e6,
            },
            "provenance": self.provenance,
            "evidence_ids": list(self.evidence_ids),
        }


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

    def execute(
        self,
        step: TaskStep | Mapping[str, Any],
        receipt: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        values = self._step_values(step)
        if context.task_id != values.get("task_id") or context.step_id != values["step_id"]:
            raise PermissionError("Request context does not own this capability step")
        context.check()
        arguments = self._validate_arguments(values["capability"], values["arguments"])
        digest = self.arguments_hash(values["capability"], arguments)
        self.ledger.consume_receipt_and_start_step(
            receipt, values["step_id"], values["capability"], digest
        )
        definition = self._definitions[values["capability"]]
        started_ns = time.time_ns()
        try:
            result = definition.executor(arguments)
            context.check()
            if not isinstance(result, dict):
                raise TypeError("Capability executor must return a JSON object")
            self.arguments_hash(values["capability"] + ":result", result)
        except Exception as exc:
            failure = classify_failure(exc)
            retryable = failure in {FailureClass.TIMEOUT, FailureClass.TRANSIENT_READ}
            target = (
                StepState.BLOCKED
                if values["safety_class"] == "read" and retryable
                else StepState.FAILED
                if values["safety_class"] == "read"
                else StepState.UNKNOWN_EFFECT
            )
            self.ledger.transition_step(
                values["step_id"],
                target,
                error=f"{type(exc).__name__}: {exc}",
                details={"failure_class": failure.value, "retryable": retryable},
            )
            raise
        evidence_ids = tuple(self._persist_evidence(values, result))
        completed_ns = time.time_ns()
        typed = CapabilityResult(
            outcome=result,
            failure_class=None,
            retryable=False,
            started_ns=started_ns,
            completed_ns=completed_ns,
            provenance={
                "capability": values["capability"],
                "arguments_hash": digest,
                "attempt_id": context.attempt_id,
                "authorization_version": context.authorization_version,
            },
            evidence_ids=evidence_ids,
        )
        durable_result = dict(result)
        durable_result["_capability_result"] = typed.as_dict()
        self.ledger.transition_step(values["step_id"], StepState.SUCCEEDED, result=durable_result)
        return result

    def _persist_evidence(self, values: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
        candidates: list[Mapping[str, Any]] = []
        evidence = result.get("evidence")
        if isinstance(evidence, Mapping):
            candidates.append(evidence)
        rows = result.get("results")
        if isinstance(rows, list):
            candidates.extend(item for item in rows if isinstance(item, Mapping))
        matches = result.get("matches")
        if isinstance(matches, list):
            candidates.extend(item for item in matches if isinstance(item, Mapping))
        ids: list[str] = []
        for item in candidates[:20]:
            url = item.get("url")
            excerpt = str(item.get("excerpt") or item.get("snippet") or item.get("content") or "")
            source_id = str(
                item.get("source_id")
                or item.get("document_id")
                or url
                or hashlib.sha256(excerpt.encode()).hexdigest()
            )
            body_hash = str(item.get("body_hash") or hashlib.sha256(excerpt.encode()).hexdigest())
            evidence_id = uuid.uuid4().hex
            record = EvidenceRecord(
                evidence_id=evidence_id,
                task_id=str(values["task_id"]),
                step_id=str(values["step_id"]),
                source_kind=str(item.get("source_kind") or values["capability"]),
                source_id=source_id,
                canonical_url=str(url) if isinstance(url, str) else None,
                title=str(item.get("title") or source_id)[:500],
                retrieved_ns=time.time_ns(),
                excerpt=excerpt[:12_000],
                body_hash=body_hash,
                relevance=(
                    float(item["score"]) if isinstance(item.get("score"), (int, float)) else None
                ),
                provenance={"capability": values["capability"]},
            )
            ids.append(self.ledger.save_evidence(record.as_dict()))
        return ids

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

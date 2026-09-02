"""Request ownership and priority scheduling for resident inference resources."""

from __future__ import annotations

import heapq
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

FOREGROUND_PRIORITY = 0
TASK_PRIORITY = 10
MODEL_PRIORITY = 20
BACKGROUND_PRIORITY = 30

InferenceProfileName = Literal["conversation", "task_plan", "task_final", "compaction", "model"]


@dataclass(frozen=True)
class InferenceProfile:
    """Per-call generation policy; profiles share one resident model and lane."""

    name: InferenceProfileName
    request_kind: str
    max_tokens: int
    temperature: float


def inference_profiles(
    default_max_tokens: int, default_temperature: float, context_length: int = 16384
) -> dict[str, InferenceProfile]:
    if not 1 <= default_max_tokens <= 4096:
        raise ValueError("Default inference token budget is invalid")
    maximum = max(1, context_length - 128)

    def budget(value: int) -> int:
        return min(value, maximum)

    return {
        "conversation": InferenceProfile(
            "conversation", "foreground", budget(default_max_tokens), default_temperature
        ),
        "task_plan": InferenceProfile(
            "task_plan", "task", budget(max(default_max_tokens, 512)), 0.0
        ),
        "task_final": InferenceProfile(
            "task_final", "task", budget(max(default_max_tokens, 768)), default_temperature
        ),
        "compaction": InferenceProfile(
            "compaction", "background", budget(max(default_max_tokens, 256)), 0.0
        ),
        "model": InferenceProfile(
            "model", "model", budget(default_max_tokens), default_temperature
        ),
    }


def priority_for(kind: str) -> int:
    """Map a descriptive call label to the shared lane's scheduling priority."""
    return {
        "foreground": FOREGROUND_PRIORITY,
        "final_stt": FOREGROUND_PRIORITY,
        "task": TASK_PRIORITY,
        "model": MODEL_PRIORITY,
        "background": BACKGROUND_PRIORITY,
    }.get(kind, MODEL_PRIORITY)


@dataclass(order=True)
class InferenceRequest:
    """One request's place in a lane and its request-owned cancellation state."""

    priority: int
    sequence: int
    request_id: str = field(compare=False)
    kind: str = field(compare=False)
    external_cancel: threading.Event = field(compare=False, repr=False)
    _lane: InferenceLane = field(compare=False, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, compare=False, repr=False)
    _cancel_active: Callable[[], None] | None = field(default=None, compare=False, repr=False)

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set() or self.external_cancel.is_set()

    def bind_active_cancel(self, callback: Callable[[], None]) -> None:
        """Bind cancellation to this request's active transport only."""
        invoke = False
        with self._lane._condition:
            if self.is_cancelled():
                invoke = True
            else:
                self._cancel_active = callback
        if invoke:
            callback()

    def unbind_active_cancel(self) -> None:
        with self._lane._condition:
            self._cancel_active = None

    def cancel(self) -> None:
        callback: Callable[[], None] | None
        with self._lane._condition:
            self._cancelled.set()
            callback = self._cancel_active
            self._lane._condition.notify_all()
        if callback is not None:
            callback()

    def __enter__(self) -> InferenceRequest:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._lane.release(self)


class InferenceLane:
    """A non-preemptive single lane whose queued foreground work runs first."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: list[InferenceRequest] = []
        self._requests: dict[str, InferenceRequest] = {}
        self._active: InferenceRequest | None = None
        self._sequence = 0
        self._closed = False

    def acquire(
        self,
        *,
        request_id: str | None = None,
        kind: str = "model",
        priority: int | None = None,
        cancel: threading.Event | None = None,
    ) -> InferenceRequest | None:
        external_cancel = cancel or threading.Event()
        if external_cancel.is_set():
            return None
        with self._condition:
            if self._closed:
                return None
            owned_id = request_id or uuid.uuid4().hex
            if owned_id in self._requests:
                raise ValueError(f"Inference request is already active: {owned_id}")
            request = InferenceRequest(
                priority_for(kind) if priority is None else priority,
                self._sequence,
                owned_id,
                kind,
                external_cancel,
                self,
            )
            self._sequence += 1
            self._requests[owned_id] = request
            heapq.heappush(self._pending, request)
            while True:
                self._discard_cancelled_locked()
                if request.is_cancelled() or self._closed:
                    self._requests.pop(owned_id, None)
                    self._condition.notify_all()
                    return None
                if self._active is None and self._pending and self._pending[0] is request:
                    heapq.heappop(self._pending)
                    self._active = request
                    return request
                self._condition.wait(0.05)

    def release(self, request: InferenceRequest) -> None:
        with self._condition:
            request._cancel_active = None
            if self._active is request:
                self._active = None
            self._requests.pop(request.request_id, None)
            self._condition.notify_all()

    def cancel(self, request_id: str) -> bool:
        with self._condition:
            request = self._requests.get(request_id)
        if request is None:
            return False
        request.cancel()
        return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            requests = list(self._requests.values())
            self._condition.notify_all()
        for request in requests:
            request.cancel()

    def _discard_cancelled_locked(self) -> None:
        while self._pending and self._pending[0].is_cancelled():
            stale = heapq.heappop(self._pending)
            self._requests.pop(stale.request_id, None)

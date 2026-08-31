"""Bounded event journal with monotonic ordering and explicit reconnect gaps."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

EPHEMERAL_KINDS = {"delta", "partial_transcription"}

State = Literal[
    "accepted", "running", "completed", "interrupted", "denied", "failed", "approval_required"
]


@dataclass(frozen=True)
class TurnEvent:
    session_id: str
    turn_id: str
    seq: int
    state: str
    kind: str
    time_ns: int
    data: dict[str, Any] = field(default_factory=dict)


class EventJournal:
    def __init__(
        self, capacity: int = 2048, sink: Callable[[str, dict[str, Any]], None] | None = None
    ):
        self.items: deque[TurnEvent] = deque(maxlen=capacity)
        self.condition = threading.Condition()
        self.seq = 0
        self.epoch = str(time.time_ns())
        self.closed = False
        self.sink = sink

    def publish(
        self, session_id: str, turn_id: str, state: str, kind: str = "state", **data
    ) -> TurnEvent:
        with self.condition:
            self.seq += 1
            event = TurnEvent(session_id, turn_id, self.seq, state, kind, time.monotonic_ns(), data)
            self.items.append(event)
            self.condition.notify_all()
        # Streaming fragments are delivered live but never force an encrypted
        # SQLite transaction. Final messages and state transitions are persisted
        # by their authoritative owners.
        if self.sink and kind not in EPHEMERAL_KINDS:
            self.sink(self.epoch, asdict(event))
        return event

    def read(
        self,
        after: int,
        timeout: float = 0,
        epoch: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with self.condition:
            reset = (epoch is not None and epoch != self.epoch) or after > self.seq
            if reset:
                after = 0
            if self.seq <= after and not self.closed and not reset:
                self.condition.wait_for(lambda: self.seq > after or self.closed, timeout=timeout)
            visible = [e for e in self.items if session_id is None or e.session_id == session_id]
            gap = bool(visible and after and after < visible[0].seq - 1)
            return {
                "events": [asdict(e) for e in visible if e.seq > after],
                "cursor": self.seq,
                "gap": gap,
                "epoch": self.epoch,
                "reset": reset,
            }

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

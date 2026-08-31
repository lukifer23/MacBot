"""Encrypted local conversation and task history with bounded retention."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings
from .tasks import (
    StepState,
    TaskState,
    recovery_disposition,
    require_step_transition,
    require_task_transition,
)

KEYCHAIN_SERVICE = "local.macbot.history"


def runtime_history_key() -> bytes | None:
    """Read the history key from the supervisor's inherited private pipe.

    The service never queries Keychain itself and never accepts the key through
    argv, an environment value, or a file. A missing descriptor disables durable
    history instead of falling back to a weaker transport.
    """
    descriptor = os.environ.get("MACBOT_HISTORY_KEY_FD")
    if descriptor is None:
        return None
    try:
        fd = int(descriptor)
        key = bytearray()
        while len(key) < 32:
            block = os.read(fd, 32 - len(key))
            if not block:
                break
            key.extend(block)
        if len(key) != 32 or os.read(fd, 1):
            raise RuntimeError("History key pipe did not contain exactly 32 bytes")
        return bytes(key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("History key pipe descriptor is invalid") from exc
    finally:
        os.environ.pop("MACBOT_HISTORY_KEY_FD", None)
        try:
            os.close(int(descriptor))
        except (OSError, TypeError, ValueError):
            pass


class HistoryStore:
    def __init__(self, settings: Settings, key: bytes, retention_days: int = 30):
        if len(key) != 32:
            raise ValueError("History encryption requires a 256-bit key")
        if not 1 <= retention_days <= 3650:
            raise ValueError("History retention must be between 1 and 3650 days")
        self.path = settings.data_dir / "history.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY, created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_id TEXT NOT NULL, ordinal INTEGER NOT NULL, role TEXT NOT NULL,
                nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, created_ns INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_session_created
                ON messages(session_id, created_ns, ordinal);
            CREATE TABLE IF NOT EXISTS summaries(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                source_ids TEXT NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
                created_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_id TEXT NOT NULL, state TEXT NOT NULL, nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL, created_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
                epoch TEXT NOT NULL, seq INTEGER NOT NULL, session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL, state TEXT NOT NULL, kind TEXT NOT NULL,
                nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, created_ns INTEGER NOT NULL,
                PRIMARY KEY(epoch, seq)
            );
            CREATE TABLE IF NOT EXISTS task_steps(
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                capability TEXT NOT NULL,
                safety_class TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                state TEXT NOT NULL,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                created_ns INTEGER NOT NULL,
                UNIQUE(task_id, ordinal),
                UNIQUE(task_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS task_steps_state ON task_steps(state);
            CREATE TABLE IF NOT EXISTS capability_receipts(
                id TEXT PRIMARY KEY,
                token_digest TEXT NOT NULL UNIQUE,
                step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
                capability TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                expires REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL,
                created_ns INTEGER NOT NULL
            );
            """
        )
        self.db.commit()
        self.aes = AESGCM(key)
        self.retention_days = retention_days
        self.lock = threading.RLock()
        self.last_cleanup_monotonic = 0.0
        self.cleanup()

    def _seal(self, table: str, row_id: str, value: Any) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        aad = f"macbot:v1:{table}:{row_id}".encode()
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        return nonce, self.aes.encrypt(nonce, payload, aad)

    def _open(self, table: str, row_id: str, nonce: bytes, ciphertext: bytes) -> Any:
        aad = f"macbot:v1:{table}:{row_id}".encode()
        return json.loads(self.aes.decrypt(nonce, ciphertext, aad))

    def _session(self, session_id: str, now: int) -> None:
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET updated_ns=excluded.updated_ns",
            (session_id, now, now),
        )

    def append_messages(
        self, session_id: str, turn_id: str, messages: Iterable[dict[str, Any]]
    ) -> list[str]:
        self._scheduled_cleanup()
        now = time.time_ns()
        ids: list[str] = []
        with self.lock, self.db:
            self._session(session_id, now)
            for ordinal, message in enumerate(messages):
                row_id = uuid.uuid4().hex
                nonce, ciphertext = self._seal("messages", row_id, message)
                self.db.execute(
                    "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)",
                    (
                        row_id,
                        session_id,
                        turn_id,
                        ordinal,
                        str(message.get("role", "unknown")),
                        nonce,
                        ciphertext,
                        now,
                    ),
                )
                ids.append(row_id)
        return ids

    def load_messages(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 2000:
            raise ValueError("Message limit out of range")
        with self.lock:
            rows = self.db.execute(
                "SELECT id,turn_id,nonce,ciphertext FROM messages WHERE session_id=? "
                "ORDER BY created_ns DESC, ordinal DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            compacted = {
                turn_id
                for (source_ids,) in self.db.execute(
                    "SELECT source_ids FROM summaries WHERE session_id=?", (session_id,)
                )
                for turn_id in json.loads(source_ids)
            }
        messages: list[dict[str, Any]] = []
        for row_id, turn_id, nonce, ciphertext in reversed(rows):
            if turn_id in compacted:
                continue
            message = self._open("messages", row_id, nonce, ciphertext)
            message["_id"] = row_id
            message["_turn_id"] = turn_id
            messages.append(message)
        return messages

    def save_summary(
        self,
        session_id: str,
        source_turn_ids: list[str],
        content: str,
        embedding: np.ndarray,
    ) -> str:
        if not source_turn_ids or len(set(source_turn_ids)) != len(source_turn_ids):
            raise ValueError("Summary source turns must be unique and nonempty")
        vector = np.asarray(embedding, dtype=np.float32)
        if vector.shape != (384,) or not np.isfinite(vector).all():
            raise ValueError("Summary embedding is invalid")
        row_id = uuid.uuid4().hex
        now = time.time_ns()
        payload = {"content": content, "embedding": vector.tolist()}
        nonce, ciphertext = self._seal("summaries", row_id, payload)
        with self.lock, self.db:
            self._session(session_id, now)
            self.db.execute(
                "INSERT INTO summaries VALUES(?,?,?,?,?,?)",
                (
                    row_id,
                    session_id,
                    json.dumps(source_turn_ids, separators=(",", ":")),
                    nonce,
                    ciphertext,
                    now,
                ),
            )
        return row_id

    def search_summaries(
        self, session_id: str, query_embedding: np.ndarray, limit: int = 3
    ) -> list[dict[str, Any]]:
        vector = np.asarray(query_embedding, dtype=np.float32)
        if vector.shape != (384,) or not np.isfinite(vector).all() or not 1 <= limit <= 10:
            raise ValueError("Summary query is invalid")
        with self.lock:
            rows = self.db.execute(
                "SELECT id,source_ids,nonce,ciphertext FROM summaries WHERE session_id=?",
                (session_id,),
            ).fetchall()
        results = []
        for row_id, source_ids, nonce, ciphertext in rows:
            payload = self._open("summaries", row_id, nonce, ciphertext)
            stored = np.asarray(payload["embedding"], dtype=np.float32)
            results.append(
                {
                    "id": row_id,
                    "source_turn_ids": json.loads(source_ids),
                    "content": payload["content"],
                    "score": float(stored @ vector),
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]

    def has_summaries(self, session_id: str) -> bool:
        with self.lock:
            return (
                self.db.execute(
                    "SELECT 1 FROM summaries WHERE session_id=? LIMIT 1", (session_id,)
                ).fetchone()
                is not None
            )

    def save_task(self, task: dict[str, Any]) -> None:
        row_id = str(task["task_id"])
        now = time.time_ns()
        nonce, ciphertext = self._seal("tasks", row_id, task)
        with self.lock, self.db:
            self._session(str(task["session_id"]), now)
            self.db.execute(
                "INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?)",
                (
                    row_id,
                    task["session_id"],
                    task["turn_id"],
                    task["state"],
                    nonce,
                    ciphertext,
                    task.get("created_ns", now),
                ),
            )

    def create_task(self, task: dict[str, Any], steps: Iterable[dict[str, Any]]) -> None:
        """Atomically write a task and its steps before any capability can run."""
        task_id = str(task["task_id"])
        session_id = str(task["session_id"])
        turn_id = str(task["turn_id"])
        state = TaskState(str(task.get("state", TaskState.PROPOSED))).value
        records = [dict(step) for step in steps]
        if any(
            str(step.get("task_id")) != task_id
            or str(step.get("session_id")) != session_id
            or str(step.get("turn_id")) != turn_id
            for step in records
        ):
            raise ValueError("Task steps must belong to the same task, session, and turn")
        ordinals = [step.get("ordinal") for step in records]
        keys = [str(step.get("idempotency_key", "")) for step in records]
        if (
            ordinals != list(range(len(records)))
            or len(keys) != len(set(keys))
            or any(not key for key in keys)
        ):
            raise ValueError("Task steps require ordered ordinals and unique idempotency keys")
        now = time.time_ns()
        task_payload = dict(task)
        task_payload["state"] = state
        task_nonce, task_ciphertext = self._seal("tasks", task_id, task_payload)
        with self.lock, self.db:
            self._session(session_id, now)
            self.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?)",
                (task_id, session_id, turn_id, state, task_nonce, task_ciphertext, now),
            )
            for record in records:
                step_id = str(record["step_id"])
                step_state = StepState(str(record.get("state", StepState.PLANNED))).value
                record["state"] = step_state
                nonce, ciphertext = self._seal("task_steps", step_id, record)
                self.db.execute(
                    "INSERT INTO task_steps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        step_id,
                        task_id,
                        session_id,
                        turn_id,
                        int(record["ordinal"]),
                        str(record["capability"]),
                        str(record["safety_class"]),
                        str(record["idempotency_key"]),
                        step_state,
                        nonce,
                        ciphertext,
                        int(record.get("created_ns", now)),
                    ),
                )

    def load_task(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT nonce,ciphertext FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        return self._open("tasks", task_id, row[0], row[1]) if row else None

    def load_steps(self, task_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,nonce,ciphertext FROM task_steps WHERE task_id=? ORDER BY ordinal",
                (task_id,),
            ).fetchall()
        return [self._open("task_steps", row[0], row[1], row[2]) for row in rows]

    def transition_task(
        self,
        task_id: str,
        target: TaskState | str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT state,nonce,ciphertext FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                raise KeyError("Unknown task")
            _, after = require_task_transition(row[0], str(target))
            payload = self._open("tasks", task_id, row[1], row[2])
            payload["state"] = after.value
            payload["updated_ns"] = time.time_ns()
            if result is not None:
                payload["result"] = result
            if error is not None:
                payload["error"] = error
            nonce, ciphertext = self._seal("tasks", task_id, payload)
            self.db.execute(
                "UPDATE tasks SET state=?,nonce=?,ciphertext=? WHERE id=? AND state=?",
                (after.value, nonce, ciphertext, task_id, row[0]),
            )
        return payload

    def list_tasks(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("Task limit out of range")
        with self.lock:
            rows = self.db.execute(
                "SELECT id,nonce,ciphertext FROM tasks WHERE session_id=? "
                "ORDER BY created_ns DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        tasks = []
        for task_id, nonce, ciphertext in rows:
            task = self._open("tasks", task_id, nonce, ciphertext)
            task["steps"] = self.load_steps(task_id)
            tasks.append(task)
        return tasks

    def transition_step(
        self,
        step_id: str,
        target: StepState | str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT state,nonce,ciphertext FROM task_steps WHERE id=?", (step_id,)
            ).fetchone()
            if not row:
                raise KeyError("Unknown task step")
            _, after = require_step_transition(row[0], str(target))
            payload = self._open("task_steps", step_id, row[1], row[2])
            payload["state"] = after.value
            if after is StepState.RUNNING:
                payload["started_ns"] = time.time_ns()
            if after in {
                StepState.SUCCEEDED,
                StepState.FAILED,
                StepState.SKIPPED,
                StepState.UNKNOWN_EFFECT,
            }:
                payload["completed_ns"] = time.time_ns()
            if result is not None:
                payload["result"] = result
            if error is not None:
                payload["error"] = error
            nonce, ciphertext = self._seal("task_steps", step_id, payload)
            self.db.execute(
                "UPDATE task_steps SET state=?,nonce=?,ciphertext=? WHERE id=? AND state=?",
                (after.value, nonce, ciphertext, step_id, row[0]),
            )
        return payload

    def recover_inflight_steps(self) -> list[dict[str, Any]]:
        """Quarantine interrupted work and classify whether retry can be automatic."""
        recovered: list[dict[str, Any]] = []
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT id,task_id,safety_class,nonce,ciphertext FROM task_steps WHERE state=?",
                (StepState.RUNNING.value,),
            ).fetchall()
            affected_tasks: set[str] = set()
            for step_id, task_id, safety_class, nonce, ciphertext in rows:
                payload = self._open("task_steps", step_id, nonce, ciphertext)
                disposition = recovery_disposition(safety_class)
                recovered_state = (
                    StepState.BLOCKED
                    if disposition.value == "retry_safe"
                    else StepState.UNKNOWN_EFFECT
                )
                payload["state"] = recovered_state.value
                payload["recovery_disposition"] = disposition.value
                payload["recovered_ns"] = time.time_ns()
                new_nonce, new_ciphertext = self._seal("task_steps", step_id, payload)
                self.db.execute(
                    "UPDATE task_steps SET state=?,nonce=?,ciphertext=? WHERE id=? AND state=?",
                    (
                        recovered_state.value,
                        new_nonce,
                        new_ciphertext,
                        step_id,
                        StepState.RUNNING.value,
                    ),
                )
                affected_tasks.add(task_id)
                recovered.append(
                    {
                        "task_id": task_id,
                        "step_id": step_id,
                        "disposition": disposition.value,
                    }
                )
            for task_id in affected_tasks:
                row = self.db.execute(
                    "SELECT state,nonce,ciphertext FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                if not row or row[0] not in {
                    TaskState.QUEUED.value,
                    TaskState.RUNNING.value,
                    TaskState.AWAITING_AUTHORIZATION.value,
                    TaskState.PAUSE_REQUESTED.value,
                    TaskState.CANCEL_REQUESTED.value,
                }:
                    continue
                payload = self._open("tasks", task_id, row[1], row[2])
                payload["state"] = TaskState.BLOCKED.value
                payload["updated_ns"] = time.time_ns()
                new_nonce, new_ciphertext = self._seal("tasks", task_id, payload)
                self.db.execute(
                    "UPDATE tasks SET state=?,nonce=?,ciphertext=? WHERE id=? AND state=?",
                    (
                        TaskState.BLOCKED.value,
                        new_nonce,
                        new_ciphertext,
                        task_id,
                        row[0],
                    ),
                )
        return recovered

    def issue_capability_receipt(
        self,
        step_id: str,
        capability: str,
        arguments_hash: str,
        authority: str,
        ttl_seconds: int,
    ) -> str:
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("Capability receipt lifetime must be 1–300 seconds")
        token = secrets.token_urlsafe(32)
        receipt_id = uuid.uuid4().hex
        now = time.time()
        payload = {
            "receipt_id": receipt_id,
            "step_id": step_id,
            "capability": capability,
            "arguments_hash": arguments_hash,
            "authority": authority,
            "issued": now,
            "expires": now + ttl_seconds,
        }
        nonce, ciphertext = self._seal("capability_receipts", receipt_id, payload)
        with self.lock, self.db:
            step = self.db.execute(
                "SELECT capability,state FROM task_steps WHERE id=?", (step_id,)
            ).fetchone()
            if not step or step[0] != capability:
                raise PermissionError("Receipt capability does not match its durable step")
            if step[1] not in {
                StepState.PLANNED.value,
                StepState.BLOCKED.value,
            }:
                raise PermissionError("Task step is not eligible for a capability receipt")
            step_row = self.db.execute(
                "SELECT nonce,ciphertext FROM task_steps WHERE id=?", (step_id,)
            ).fetchone()
            step_payload = self._open("task_steps", step_id, step_row[0], step_row[1])
            step_payload["state"] = StepState.AUTHORIZED.value
            step_payload["authorized_ns"] = time.time_ns()
            step_nonce, step_ciphertext = self._seal("task_steps", step_id, step_payload)
            self.db.execute(
                "UPDATE task_steps SET state=?,nonce=?,ciphertext=? WHERE id=? AND state=?",
                (
                    StepState.AUTHORIZED.value,
                    step_nonce,
                    step_ciphertext,
                    step_id,
                    step[1],
                ),
            )
            self.db.execute(
                "INSERT INTO capability_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    step_id,
                    capability,
                    arguments_hash,
                    payload["expires"],
                    0,
                    nonce,
                    ciphertext,
                    time.time_ns(),
                ),
            )
        return receipt_id + "." + token

    def consume_capability_receipt(
        self, receipt: str, step_id: str, capability: str, arguments_hash: str
    ) -> dict[str, Any]:
        try:
            receipt_id, token = receipt.split(".", 1)
        except ValueError as exc:
            raise PermissionError("Capability receipt is invalid") from exc
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT token_digest,step_id,capability,arguments_hash,expires,consumed,nonce,ciphertext "
                "FROM capability_receipts WHERE id=?",
                (receipt_id,),
            ).fetchone()
            if (
                not row
                or not secrets.compare_digest(row[0], digest)
                or row[1] != step_id
                or row[2] != capability
                or row[3] != arguments_hash
                or row[4] <= time.time()
                or row[5]
            ):
                raise PermissionError("Capability receipt is expired, consumed, or mismatched")
            changed = self.db.execute(
                "UPDATE capability_receipts SET consumed=1 WHERE id=? AND consumed=0",
                (receipt_id,),
            ).rowcount
            if changed != 1:
                raise PermissionError("Capability receipt was already consumed")
            return self._open("capability_receipts", receipt_id, row[6], row[7])

    def requeue_recovered_read_step(self, step_id: str) -> dict[str, Any]:
        """Requeue only a read step that recovery classified as safe to repeat."""
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT state,nonce,ciphertext FROM task_steps WHERE id=?", (step_id,)
            ).fetchone()
            if not row:
                raise KeyError("Unknown task step")
            payload = self._open("task_steps", step_id, row[1], row[2])
            if (
                row[0] != StepState.BLOCKED.value
                or payload.get("recovery_disposition") != "retry_safe"
            ):
                raise PermissionError("Only retry-safe recovered reads may be requeued")
            payload["state"] = StepState.PLANNED.value
            payload["requeued_ns"] = time.time_ns()
            nonce, ciphertext = self._seal("task_steps", step_id, payload)
            self.db.execute(
                "UPDATE task_steps SET state=?,nonce=?,ciphertext=? WHERE id=? AND state=?",
                (
                    StepState.PLANNED.value,
                    nonce,
                    ciphertext,
                    step_id,
                    StepState.BLOCKED.value,
                ),
            )
        return payload

    def save_event(self, epoch: str, event: dict[str, Any]) -> None:
        row_id = f"{epoch}:{event['seq']}"
        nonce, ciphertext = self._seal("events", row_id, event.get("data", {}))
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    epoch,
                    event["seq"],
                    event["session_id"],
                    event["turn_id"],
                    event["state"],
                    event["kind"],
                    nonce,
                    ciphertext,
                    time.time_ns(),
                ),
            )

    def clear_session(self, session_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self.db.execute("DELETE FROM events WHERE session_id=?", (session_id,))

    def clear_conversation(self, session_id: str) -> None:
        """Clear conversational memory while retaining the durable Task ledger."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self.db.execute("DELETE FROM summaries WHERE session_id=?", (session_id,))
            self.db.execute(
                "DELETE FROM events WHERE session_id=? AND kind NOT LIKE 'task%'",
                (session_id,),
            )

    def clear_all(self) -> None:
        with self.lock, self.db:
            for table in ("messages", "summaries", "tasks", "sessions", "events"):
                self.db.execute(f"DELETE FROM {table}")

    def cleanup(self) -> None:
        cutoff = time.time_ns() - self.retention_days * 86_400 * 1_000_000_000
        with self.lock, self.db:
            self.db.execute("DELETE FROM messages WHERE created_ns < ?", (cutoff,))
            self.db.execute("DELETE FROM summaries WHERE created_ns < ?", (cutoff,))
            self.db.execute("DELETE FROM tasks WHERE created_ns < ?", (cutoff,))
            self.db.execute("DELETE FROM sessions WHERE updated_ns < ?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE created_ns < ?", (cutoff,))
            self.db.execute("DELETE FROM capability_receipts WHERE expires < ?", (time.time(),))
        self.last_cleanup_monotonic = time.monotonic()

    def _scheduled_cleanup(self) -> None:
        if time.monotonic() - self.last_cleanup_monotonic >= 3600:
            self.cleanup()

    def close(self) -> None:
        with self.lock:
            self.db.close()

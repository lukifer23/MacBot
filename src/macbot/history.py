"""Encrypted local conversation and task history with bounded retention."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings

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
            """
        )
        self.db.commit()
        self.aes = AESGCM(key)
        self.retention_days = retention_days
        self.lock = threading.RLock()
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

    def clear_all(self) -> None:
        with self.lock, self.db:
            for table in ("messages", "summaries", "tasks", "sessions", "events"):
                self.db.execute(f"DELETE FROM {table}")

    def cleanup(self) -> None:
        cutoff = time.time_ns() - self.retention_days * 86_400 * 1_000_000_000
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE updated_ns < ?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE created_ns < ?", (cutoff,))

    def close(self) -> None:
        with self.lock:
            self.db.close()

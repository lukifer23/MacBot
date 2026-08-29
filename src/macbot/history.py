"""Encrypted local conversation and task history with bounded retention."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings

KEYCHAIN_SERVICE = "local.macbot.history"


def runtime_history_key() -> bytes | None:
    """Read a key from an inherited pipe, or an existing Keychain item.

    A missing key disables durable history; it never creates a plaintext store or
    exposes a new secret through argv, the environment, or a file.
    """
    descriptor = os.environ.get("MACBOT_HISTORY_KEY_FD")
    if descriptor:
        try:
            fd = int(descriptor)
            key = os.read(fd, 32)
            os.close(fd)
            if len(key) != 32:
                raise RuntimeError("History key pipe did not contain 32 bytes")
            return key
        finally:
            os.environ.pop("MACBOT_HISTORY_KEY_FD", None)
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode:
        return None
    try:
        key = base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError as exc:
        raise RuntimeError("The MacBot history Keychain item is invalid") from exc
    if len(key) != 32:
        raise RuntimeError("The MacBot history Keychain key has an invalid length")
    return key


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
                "SELECT id,nonce,ciphertext FROM messages WHERE session_id=? "
                "ORDER BY created_ns DESC, ordinal DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._open("messages", row[0], row[1], row[2]) for row in reversed(rows)]

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

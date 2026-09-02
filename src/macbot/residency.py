"""Host-wide ownership for the single production inference stack."""

from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO


class InferenceResidencyConflict(RuntimeError):
    """Raised when another supervisor already owns model residency on this host."""

    def __init__(self, owner: dict[str, object] | None = None):
        self.owner = owner or {}
        detail = ""
        if self.owner:
            pid = self.owner.get("pid", "unknown")
            data_dir = self.owner.get("data_dir", "unknown")
            detail = f" (pid {pid}, data directory {data_dir})"
        super().__init__("Another MacBot inference stack owns this host" + detail)


def default_residency_dir() -> Path:
    """Return the one per-user, host-wide lease directory."""
    return Path.home() / "Library/Application Support/MacBot/run"


class InferenceResidencyLease:
    """An exclusive kernel-backed lease held for a supervisor's whole lifetime."""

    def __init__(
        self,
        data_dir: Path,
        *,
        purpose: str = "production",
        lease_dir: Path | None = None,
    ):
        self.data_dir = data_dir.expanduser().resolve()
        self.purpose = purpose
        self.lease_dir = lease_dir or default_residency_dir()
        self.path = self.lease_dir / "inference-residency.lock"
        self._file: TextIO | None = None
        self.owner: dict[str, object] | None = None

    def _prepare_directory(self) -> None:
        self.lease_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        stat = self.lease_dir.lstat()
        if self.lease_dir.is_symlink() or stat.st_uid != os.getuid():
            raise RuntimeError("MacBot residency directory is not owned by the current user")
        os.chmod(self.lease_dir, 0o700)

    @staticmethod
    def _read_owner(file: TextIO) -> dict[str, object] | None:
        try:
            file.seek(0)
            value = json.load(file)
        except (json.JSONDecodeError, OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def acquire(self) -> dict[str, object]:
        if self._file is not None:
            raise RuntimeError("Inference residency lease is already acquired")
        self._prepare_directory()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        lease_file = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            fcntl.flock(lease_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = self._read_owner(lease_file)
            lease_file.close()
            raise InferenceResidencyConflict(owner) from None
        owner = {
            "pid": os.getpid(),
            "acquired_unix": time.time(),
            "acquired_at": datetime.now(UTC).isoformat(),
            "data_dir": str(self.data_dir),
            "purpose": self.purpose,
        }
        lease_file.seek(0)
        lease_file.truncate()
        json.dump(owner, lease_file, sort_keys=True)
        lease_file.flush()
        os.fsync(lease_file.fileno())
        self._file = lease_file
        self.owner = owner
        return dict(owner)

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file, fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self.owner = None

    def __enter__(self) -> InferenceResidencyLease:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

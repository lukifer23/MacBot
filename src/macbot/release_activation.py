"""Transactional activation of one paired native app/runtime generation."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .residency import InferenceResidencyLease


@dataclass(frozen=True)
class ActivationReceipt:
    release: Path
    previous: Path | None
    previous_rollback: Path | None
    legacy_app_backup: Path | None
    legacy_runtime_backup: Path | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "release": str(self.release),
                "previous": str(self.previous) if self.previous else None,
                "previous_rollback": (
                    str(self.previous_rollback) if self.previous_rollback else None
                ),
                "legacy_app_backup": (
                    str(self.legacy_app_backup) if self.legacy_app_backup else None
                ),
                "legacy_runtime_backup": (
                    str(self.legacy_runtime_backup) if self.legacy_runtime_backup else None
                ),
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> ActivationReceipt:
        data = json.loads(value)
        return cls(
            release=Path(data["release"]),
            previous=Path(data["previous"]) if data["previous"] else None,
            previous_rollback=(
                Path(data["previous_rollback"]) if data["previous_rollback"] else None
            ),
            legacy_app_backup=(
                Path(data["legacy_app_backup"]) if data["legacy_app_backup"] else None
            ),
            legacy_runtime_backup=(
                Path(data["legacy_runtime_backup"]) if data["legacy_runtime_backup"] else None
            ),
        )


def _replace_symlink(link: Path, target: Path) -> None:
    candidate = link.with_name(link.name + ".next")
    candidate.unlink(missing_ok=True)
    candidate.symlink_to(target)
    os.replace(candidate, link)


def _resolved_link(path: Path) -> Path | None:
    return path.resolve(strict=True) if path.is_symlink() else None


def activate(
    release: Path,
    app_target: Path,
    runtime_target: Path,
    current: Path,
    rollback: Path,
    *,
    data_dir: Path,
) -> ActivationReceipt:
    """Atomically select a verified pair while no inference stack can start."""
    release = release.resolve(strict=True)
    required = [
        release / "app/MacBot.app",
        release / "runtime/bin/macbot",
        release / "release-manifest.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Release generation is incomplete: " + ", ".join(missing))

    lease = InferenceResidencyLease(data_dir, purpose="release-activation")
    lease.acquire()
    legacy_backup: Path | None = None
    legacy_runtime_backup: Path | None = None
    previous = _resolved_link(current)
    previous_rollback = _resolved_link(rollback)
    try:
        app_target.parent.mkdir(parents=True, exist_ok=True)
        if app_target.exists() and not app_target.is_symlink():
            legacy_backup = app_target.parent / f"MacBot.previous-{time.time_ns()}.app"
            os.replace(app_target, legacy_backup)
        if runtime_target.exists() and not runtime_target.is_symlink():
            legacy_runtime_backup = runtime_target.with_name(f"runtime.previous-{time.time_ns()}")
            os.replace(runtime_target, legacy_runtime_backup)
        _replace_symlink(runtime_target, current / "runtime")
        _replace_symlink(app_target, current / "app/MacBot.app")
        if previous is not None:
            _replace_symlink(rollback, previous)
        _replace_symlink(current, release)
    except Exception:
        if previous is not None:
            _replace_symlink(current, previous)
        elif current.is_symlink():
            current.unlink()
        if previous_rollback is not None:
            _replace_symlink(rollback, previous_rollback)
        elif rollback.is_symlink():
            rollback.unlink()
        if legacy_backup is not None and legacy_backup.exists():
            app_target.unlink(missing_ok=True)
            os.replace(legacy_backup, app_target)
        if legacy_runtime_backup is not None and legacy_runtime_backup.exists():
            runtime_target.unlink(missing_ok=True)
            os.replace(legacy_runtime_backup, runtime_target)
        raise
    finally:
        lease.release()
    return ActivationReceipt(
        release, previous, previous_rollback, legacy_backup, legacy_runtime_backup
    )


def restore(receipt: ActivationReceipt, current: Path, rollback: Path, *, data_dir: Path) -> None:
    """Restore the exact pointers changed by ``activate`` after failed validation."""
    lease = InferenceResidencyLease(data_dir, purpose="release-rollback")
    lease.acquire()
    try:
        selected = _resolved_link(current)
        if selected != receipt.release.resolve():
            raise RuntimeError(
                "Refusing rollback because the active generation changed after activation"
            )
        if receipt.previous is not None:
            _replace_symlink(current, receipt.previous)
        elif current.is_symlink():
            current.unlink()
        if receipt.previous_rollback is not None:
            _replace_symlink(rollback, receipt.previous_rollback)
        elif rollback.is_symlink():
            rollback.unlink()
        if receipt.previous is None and receipt.legacy_app_backup is not None:
            app_target = receipt.legacy_app_backup.parent / "MacBot.app"
            app_target.unlink(missing_ok=True)
            os.replace(receipt.legacy_app_backup, app_target)
        if receipt.previous is None and receipt.legacy_runtime_backup is not None:
            runtime_target = receipt.legacy_runtime_backup.parent / "runtime"
            runtime_target.unlink(missing_ok=True)
            os.replace(receipt.legacy_runtime_backup, runtime_target)
    finally:
        lease.release()


def _main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    activate_parser = commands.add_parser("activate")
    activate_parser.add_argument("release", type=Path)
    activate_parser.add_argument("app_target", type=Path)
    activate_parser.add_argument("runtime_target", type=Path)
    activate_parser.add_argument("current", type=Path)
    activate_parser.add_argument("rollback", type=Path)
    activate_parser.add_argument("--data-dir", type=Path, required=True)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("receipt", type=Path)
    restore_parser.add_argument("current", type=Path)
    restore_parser.add_argument("rollback", type=Path)
    restore_parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "activate":
        receipt = activate(
            args.release,
            args.app_target,
            args.runtime_target,
            args.current,
            args.rollback,
            data_dir=args.data_dir,
        )
        print(receipt.to_json())
    else:
        receipt = ActivationReceipt.from_json(args.receipt.read_text())
        restore(receipt, args.current, args.rollback, data_dir=args.data_dir)


if __name__ == "__main__":
    _main()

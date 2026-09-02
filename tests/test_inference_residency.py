"""Exercise the real host-wide inference lease across independent data roots."""

import json
import subprocess
import sys

import pytest

from macbot.config import Settings
from macbot.orchestrator import MacBotOrchestrator
from macbot.residency import InferenceResidencyConflict, InferenceResidencyLease


def test_residency_lease_excludes_a_second_process_and_data_root(tmp_path):
    lease_dir = tmp_path / "host-lease"
    child_data = tmp_path / "child-state"
    script = """
import json, pathlib, sys
from macbot.residency import InferenceResidencyLease
lease = InferenceResidencyLease(pathlib.Path(sys.argv[1]), lease_dir=pathlib.Path(sys.argv[2]))
print(json.dumps(lease.acquire()), flush=True)
sys.stdin.read(1)
lease.release()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(child_data), str(lease_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdin is not None
    owner = json.loads(child.stdout.readline())
    contender = InferenceResidencyLease(tmp_path / "parent-state", lease_dir=lease_dir)
    try:
        with pytest.raises(InferenceResidencyConflict) as conflict:
            contender.acquire()
        assert conflict.value.owner == owner
        assert owner["pid"] == child.pid
    finally:
        child.stdin.close()
        child.wait(timeout=5)
        contender.release()


def test_different_data_directories_cannot_own_two_inference_stacks(tmp_path):
    lease_dir = tmp_path / "host-lease"
    first = MacBotOrchestrator(Settings(data_dir=tmp_path / "production"), residency_dir=lease_dir)
    second = MacBotOrchestrator(
        Settings(data_dir=tmp_path / "verification"), residency_dir=lease_dir
    )
    try:
        first.acquire()
        with pytest.raises(InferenceResidencyConflict) as conflict:
            second.acquire()
        assert conflict.value.owner["pid"] == first._residency_lease.owner["pid"]
        assert conflict.value.owner["data_dir"] == str((tmp_path / "production").resolve())
        assert second._instance_file is None
    finally:
        first._residency_lease.release()
        if first._instance_file:
            first._instance_file.close()
        second._residency_lease.release()
        first.client.close()
        first.auth.close()
        second.client.close()
        second.auth.close()


def test_released_host_lease_admits_exactly_one_successor(tmp_path):
    lease_dir = tmp_path / "host-lease"
    first = InferenceResidencyLease(tmp_path / "production", lease_dir=lease_dir)
    successor = InferenceResidencyLease(tmp_path / "verification", lease_dir=lease_dir)
    first.acquire()
    first.release()
    owner = successor.acquire()
    try:
        assert owner["data_dir"] == str((tmp_path / "verification").resolve())
        assert json.loads(successor.path.read_text()) == owner
    finally:
        successor.release()


def test_data_directory_lock_failure_releases_host_lease(tmp_path):
    lease_dir = tmp_path / "host-lease"
    first = MacBotOrchestrator(Settings(data_dir=tmp_path / "same"), residency_dir=lease_dir)
    blocked = MacBotOrchestrator(Settings(data_dir=tmp_path / "same"), residency_dir=lease_dir)
    successor = MacBotOrchestrator(Settings(data_dir=tmp_path / "other"), residency_dir=lease_dir)
    try:
        first.acquire()
        first._residency_lease.release()
        with pytest.raises(RuntimeError, match="owns this data directory"):
            blocked.acquire()
        successor.acquire()
    finally:
        for supervisor in (first, blocked, successor):
            supervisor._residency_lease.release()
            if supervisor._instance_file:
                supervisor._instance_file.close()
            supervisor.client.close()
            supervisor.auth.close()

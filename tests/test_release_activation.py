from pathlib import Path

import pytest

from macbot.release_activation import activate, restore
from macbot.residency import InferenceResidencyConflict, InferenceResidencyLease


@pytest.fixture(autouse=True)
def isolated_host_lease(tmp_path, monkeypatch):
    lease_dir = tmp_path / "host-lease"
    monkeypatch.setattr("macbot.residency.default_residency_dir", lambda: lease_dir)


def generation(root: Path, name: str) -> Path:
    release = root / "releases" / name
    (release / "app/MacBot.app").mkdir(parents=True)
    (release / "runtime/bin").mkdir(parents=True)
    (release / "runtime/bin/macbot").write_text("runtime")
    (release / "release-manifest.json").write_text("{}")
    return release


def test_activation_swaps_one_paired_pointer_and_restore_is_exact(tmp_path):
    old = generation(tmp_path, "old")
    prior_rollback = generation(tmp_path, "older")
    new = generation(tmp_path, "new")
    current = tmp_path / "current"
    rollback = tmp_path / "rollback"
    current.symlink_to(old)
    rollback.symlink_to(prior_rollback)
    app = tmp_path / "MacBot.app"
    runtime = tmp_path / "runtime"

    receipt = activate(new, app, runtime, current, rollback, data_dir=tmp_path / "state")

    assert current.resolve() == new
    assert rollback.resolve() == old
    assert app.resolve() == new / "app/MacBot.app"
    assert runtime.resolve() == new / "runtime"

    restore(receipt, current, rollback, data_dir=tmp_path / "state")
    assert current.resolve() == old
    assert rollback.resolve() == prior_rollback
    assert app.resolve() == old / "app/MacBot.app"
    assert runtime.resolve() == old / "runtime"


def test_activation_refuses_to_swap_while_inference_is_resident(tmp_path, monkeypatch):
    release = generation(tmp_path, "new")
    lease_dir = tmp_path / "lease"
    monkeypatch.setattr("macbot.residency.default_residency_dir", lambda: lease_dir)
    owner = InferenceResidencyLease(tmp_path / "active", lease_dir=lease_dir)
    owner.acquire()
    try:
        with pytest.raises(InferenceResidencyConflict):
            activate(
                release,
                tmp_path / "MacBot.app",
                tmp_path / "runtime",
                tmp_path / "current",
                tmp_path / "rollback",
                data_dir=tmp_path / "state",
            )
    finally:
        owner.release()
    assert not (tmp_path / "current").exists()


def test_incomplete_generation_never_changes_current(tmp_path):
    old = generation(tmp_path, "old")
    incomplete = tmp_path / "releases/incomplete"
    incomplete.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(old)

    with pytest.raises(FileNotFoundError, match="incomplete"):
        activate(
            incomplete,
            tmp_path / "MacBot.app",
            tmp_path / "runtime",
            current,
            tmp_path / "rollback",
            data_dir=tmp_path / "state",
        )
    assert current.resolve() == old


def test_restore_refuses_to_overwrite_a_newer_activation(tmp_path):
    old = generation(tmp_path, "old")
    selected = generation(tmp_path, "selected")
    newer = generation(tmp_path, "newer")
    current = tmp_path / "current"
    rollback = tmp_path / "rollback"
    current.symlink_to(old)
    receipt = activate(
        selected,
        tmp_path / "MacBot.app",
        tmp_path / "runtime",
        current,
        rollback,
        data_dir=tmp_path / "state",
    )
    current.unlink()
    current.symlink_to(newer)

    with pytest.raises(RuntimeError, match="active generation changed"):
        restore(receipt, current, rollback, data_dir=tmp_path / "state")
    assert current.resolve() == newer


def test_failed_first_activation_can_restore_legacy_app_and_runtime(tmp_path):
    selected = generation(tmp_path, "selected")
    app = tmp_path / "MacBot.app"
    runtime = tmp_path / "runtime"
    app.mkdir()
    runtime.mkdir()
    (app / "legacy-app").write_text("app")
    (runtime / "legacy-runtime").write_text("runtime")
    current = tmp_path / "current"
    rollback = tmp_path / "rollback"

    receipt = activate(selected, app, runtime, current, rollback, data_dir=tmp_path / "state")
    restore(receipt, current, rollback, data_dir=tmp_path / "state")

    assert not current.exists()
    assert not app.is_symlink() and (app / "legacy-app").read_text() == "app"
    assert not runtime.is_symlink()
    assert (runtime / "legacy-runtime").read_text() == "runtime"

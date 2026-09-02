from types import SimpleNamespace

import pytest

from macbot.native_ipc import NativeIPCServer


class TaskEngineStub:
    def list(self, session_id):
        assert session_id == "native"
        return [{"task_id": "one", "state": "paused", "commands": ["resume", "cancel"]}]


def server_with_tasks():
    server = NativeIPCServer.__new__(NativeIPCServer)
    server.runtime = SimpleNamespace(task_engine=TaskEngineStub())
    return server


def test_native_task_operations_fail_closed_without_protocol_v2():
    server = server_with_tasks()
    for version in (None, 1, 2, "3"):
        request = {"op": "task_list"}
        if version is not None:
            request["protocol_version"] = version
        with pytest.raises(ValueError, match="protocol_version 3"):
            server._dispatch(request)


def test_native_task_list_returns_protocol_and_authoritative_commands():
    response = server_with_tasks()._dispatch({"op": "task_list", "protocol_version": 3})
    assert response == {
        "protocol_version": 3,
        "tasks": [{"task_id": "one", "state": "paused", "commands": ["resume", "cancel"]}],
    }

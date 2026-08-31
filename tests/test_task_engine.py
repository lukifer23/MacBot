import os
import time

from macbot.auth import AuthStore
from macbot.config import Settings, prepare
from macbot.events import EventJournal
from macbot.history import HistoryStore
from macbot.task_engine import TaskEngine
from macbot.tasks import Intent, PlannedAction
from macbot.tools import Tools


class BoundedPlanner:
    def route(self, text, cancel):
        assert text == "Check this Mac"
        return Intent(
            "act",
            (
                PlannedAction("local_time", {}, text, "read"),
                PlannedAction("system_info", {}, text, "read"),
            ),
        )


class ResultSummarizer:
    def stream(self, messages, tools, cancel, schema=None):
        yield {"content": "The requested local checks completed with recorded results."}


def wait_for_terminal(engine, task_id, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = engine.history.load_task(task_id)
        if task and task["state"] in {"completed", "partial", "failed", "blocked", "cancelled"}:
            return task
        time.sleep(0.01)
    raise AssertionError(engine.history.load_task(task_id))


def test_explicit_task_is_authorized_once_and_executes_through_durable_broker(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    engine = TaskEngine(history, events, tools, BoundedPlanner(), ResultSummarizer())
    try:
        proposal = engine.create("Check this Mac", "native")
        assert proposal["state"] == "awaiting_authorization"
        assert [step["state"] for step in proposal["steps"]] == ["planned", "planned"]

        queued = engine.authorize(proposal["task_id"], "native", True)
        assert queued["state"] == "queued"
        completed = wait_for_terminal(engine, proposal["task_id"])
        assert completed["state"] == "completed"
        steps = history.load_steps(proposal["task_id"])
        assert [step["state"] for step in steps] == ["succeeded", "succeeded"]
        assert steps[0]["result"]["source"] == "mac_clock"
        assert 0 <= steps[1]["result"]["memory_percent"] <= 100
        assert completed["result"]["summary"]
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()


def test_task_ownership_and_denial_are_session_scoped(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    engine = TaskEngine(history, events, tools, BoundedPlanner(), ResultSummarizer())
    try:
        proposal = engine.create("Check this Mac", "native")
        try:
            engine.authorize(proposal["task_id"], "other-session", True)
        except PermissionError:
            pass
        else:
            raise AssertionError("A different session authorized the Task")
        denied = engine.authorize(proposal["task_id"], "native", False)
        assert denied["state"] == "cancelled"
        assert history.load_steps(proposal["task_id"])[0]["state"] == "planned"
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()

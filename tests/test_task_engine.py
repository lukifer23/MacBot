import json
import os
import threading
import time

import pytest

from macbot.auth import AuthStore
from macbot.config import Settings, prepare
from macbot.events import EventJournal
from macbot.history import HistoryStore
from macbot.intent import IntentRouter
from macbot.research_context import replan_context, research_sources
from macbot.task_engine import TaskEngine
from macbot.tasks import Intent, PlannedAction, TaskPlan
from macbot.tools import Tools


class BoundedPlanner:
    def route(self, text, cancel):
        assert text == "Check this Mac"
        return Intent(
            "act",
            (
                PlannedAction("rag_search", {"query": "MacBot"}, text, "read"),
                PlannedAction("web_search", {"query": "MacBot"}, text, "read"),
            ),
        )


class ResultSummarizer:
    def stream(self, messages, tools, cancel, schema=None, **_request):
        yield {"content": "The requested local checks completed with recorded results."}


class FailingPlanner:
    def route(self, text, cancel):
        raise RuntimeError("planner unavailable")


def recorded_search(name, arguments):
    return {
        "status": "completed",
        "source": name,
        "results": [
            {
                "id": "local-1",
                "provenance": {"title": "MacBot notes"},
                "content": "MacBot is running locally.",
                "url": "https://example.org/macbot",
                "snippet": "Documentation for MacBot.",
            }
        ],
    }


class BlockingSummarizer:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def stream(self, messages, tools, cancel, schema=None, **_request):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            assert self.release.wait(2)
        yield {"content": "durable summary"}


class ReplanningPlanner:
    def __init__(self):
        self.replans = 0

    def route(self, text, cancel):
        return Intent("act", (PlannedAction("rag_search", {"query": "local"}, text, "read"),))

    def replan(self, text, observations, cancel):
        self.replans += 1
        if self.replans == 1:
            return Intent(
                "act", (PlannedAction("web_search", {"query": "external"}, text, "read"),)
            )
        return Intent("clarify", clarification="Evidence is sufficient.")


def test_recorded_sources_filter_urls_deduplicate_results_and_bound_excerpts():
    web = {"title": "Official source", "url": "https://example.org/report", "snippet": "x" * 1000}
    local = {
        "id": "passage-2",
        "content": "Local evidence",
        "provenance": {"title": "Operator guide"},
    }
    observations = [
        {
            "tool": "web_search",
            "state": "succeeded",
            "result": {"results": [web, web, {"url": "file:///private/report"}]},
        },
        {"tool": "rag_search", "state": "succeeded", "result": {"results": [local]}},
    ]
    sources = research_sources(observations)
    assert [source["id"] for source in sources] == ["S1", "S2"]
    assert sources[0]["url"] == "https://example.org/report"
    assert len(sources[0]["excerpt"]) == 480
    assert sources[1]["title"] == "Operator guide"
    assert sources[1]["url"] is None


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
    tools._execute = lambda name, arguments: {
        "status": "completed",
        "source": name,
        "query": arguments["query"],
        "results": [],
    }
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
        assert [step["result"]["source"] for step in steps] == ["rag_search", "web_search"]
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
    tools._execute = lambda name, arguments: {
        "status": "completed",
        "source": name,
        "query": arguments["query"],
        "results": [],
    }
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


def test_task_proposal_exists_before_planning_and_records_planner_failure(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    engine = TaskEngine(history, events, tools, FailingPlanner(), ResultSummarizer())
    try:
        try:
            engine.create("Check this Mac", "native")
        except RuntimeError as exc:
            assert str(exc) == "planner unavailable"
        else:
            raise AssertionError("Planner failure was not surfaced")
        tasks = history.list_tasks("native")
        assert len(tasks) == 1
        assert tasks[0]["state"] == "failed"
        assert tasks[0]["planning_attempts"] == 1
        assert tasks[0]["original_text"] == "Check this Mac"
        assert tasks[0]["error"] == "RuntimeError: planner unavailable"
        assert tasks[0]["steps"] == []
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()


def test_cancel_during_final_synthesis_resolves_partial_and_worker_survives(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    tools._execute = recorded_search
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    summarizer = BlockingSummarizer()
    engine = TaskEngine(history, events, tools, BoundedPlanner(), summarizer)
    try:
        first = engine.create("Check this Mac", "native")
        engine.authorize(first["task_id"], "native", True)
        assert summarizer.started.wait(2)
        engine.cancel(first["task_id"], "native")
        summarizer.release.set()
        assert wait_for_terminal(engine, first["task_id"])["state"] == "partial"
        assert engine.worker.is_alive()

        second = engine.create("Check this Mac", "native")
        engine.authorize(second["task_id"], "native", True)
        assert wait_for_terminal(engine, second["task_id"])["state"] == "completed"
        assert engine.worker.is_alive()
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()


@pytest.mark.parametrize("command", ["resume", "cancel"])
def test_pause_during_final_synthesis_releases_lane_before_next_command(tmp_path, command):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    tools._execute = recorded_search
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    summarizer = BlockingSummarizer()
    engine = TaskEngine(history, events, tools, BoundedPlanner(), summarizer)
    try:
        proposal = engine.create("Check this Mac", "native")
        engine.authorize(proposal["task_id"], "native", True)
        assert summarizer.started.wait(2)
        engine.pause(proposal["task_id"], "native")
        summarizer.release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            paused = history.load_task(proposal["task_id"])
            if paused and paused["state"] == "paused":
                break
            time.sleep(0.01)
        else:
            raise AssertionError(history.load_task(proposal["task_id"]))
        task_id = proposal["task_id"]
        if command == "resume":
            engine.resume(task_id, "native")
            assert wait_for_terminal(engine, task_id)["state"] == "completed"
        else:
            stopped = engine.cancel(task_id, "native")
            assert stopped["state"] == "partial"
            assert stopped["result"]["summary"].startswith("Task stopped.")
            assert len(stopped["result"]["steps"]) == 2
            assert summarizer.calls == 1
            assert history.load_task(task_id)["state"] == "partial"
        assert engine.worker.is_alive()
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()


def test_transient_read_retries_once_with_durable_attempt_count(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    calls = 0

    def execute(name, arguments):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary transport loss")
        return {"status": "completed", "source": name}

    tools._execute = execute
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    engine = TaskEngine(history, events, tools, BoundedPlanner(), ResultSummarizer())
    try:
        proposal = engine.create("Check this Mac", "native")
        engine.authorize(proposal["task_id"], "native", True)
        assert wait_for_terminal(engine, proposal["task_id"])["state"] == "completed"
        steps = history.load_steps(proposal["task_id"])
        assert steps[0]["attempts"] == 2
        assert steps[0]["failure_class"] == "transient_read"
        assert calls == 3  # retried first step, then executed the second step once
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()


def test_observation_driven_replan_requires_fresh_authorization(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)

    def execute(name, arguments):
        return {
            "status": "no_answer" if name == "rag_search" else "completed",
            "source": name,
            "results": [],
        }

    tools._execute = execute
    history = HistoryStore(settings, os.urandom(32))
    events = EventJournal(sink=history.save_event)
    planner = ReplanningPlanner()
    engine = TaskEngine(history, events, tools, planner, ResultSummarizer())
    try:
        proposal = engine.create("Check this Mac", "native")
        engine.authorize(proposal["task_id"], "native", True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            replanned = history.load_task(proposal["task_id"])
            if replanned and replanned["state"] == "awaiting_authorization":
                break
            time.sleep(0.01)
        else:
            raise AssertionError(history.load_task(proposal["task_id"]))
        assert replanned["planning_attempts"] == 2
        assert replanned["replan_budget"] == 1
        assert [step["capability"] for step in history.load_steps(proposal["task_id"])] == [
            "rag_search",
            "web_search",
        ]
        engine.authorize(proposal["task_id"], "native", True)
        assert wait_for_terminal(engine, proposal["task_id"])["state"] == "completed"
    finally:
        engine.close()
        events.close()
        history.close()
        tools.close()
        auth.close()


def test_planner_dependencies_are_resolved_to_durable_step_ids(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(tmp_path)
    tools = Tools(settings, auth)
    try:
        router = IntentRouter(ResultSummarizer(), tools)
        request = "Search my documents for MacBot, then search the web for MacBot."
        actions = [
            {
                "name": "rag_search",
                "arguments": {"query": "MacBot"},
                "source_span": "Search my documents for MacBot",
                "depends_on": [],
            },
            {
                "name": "web_search",
                "arguments": {"query": "MacBot"},
                "source_span": "search the web for MacBot",
                "depends_on": [0],
            },
        ]
        payload = {"mode": "act", "actions": actions, "clarification": ""}
        intent = router.parse(json.dumps(payload), request)
        steps = TaskPlan.from_intent("native", "test-turn", request, intent).durable_steps()
        assert steps[0].depends_on == ()
        assert steps[1].depends_on == (steps[0].step_id,)
        for invalid in ([1], [True], [-1], [0, 0]):
            actions[1]["depends_on"] = invalid
            with pytest.raises(ValueError, match="distinct earlier actions"):
                router.parse(json.dumps(payload), request)
    finally:
        tools.close()
        auth.close()


def test_replan_keeps_bounded_evidence_and_omits_raw_tool_output():
    result = recorded_search("web_search", {"query": "MacBot"})
    result["raw_provider_body"] = "private provider payload" * 100
    payload = replan_context(
        [
            {
                "capability": "web_search",
                "state": "succeeded",
                "result": result,
                "arguments": {"query": "MacBot"},
            }
        ],
        500,
    )
    assert len(payload) <= 500
    assert "private provider payload" not in payload
    parsed = json.loads(payload)
    assert parsed["outcomes"][0]["attempted_arguments"] == {"query": "MacBot"}
    assert parsed["sources"][0]["excerpt"] == "Documentation for MacBot."

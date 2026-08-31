"""Actual local inference and retrieval services exercising the shared runtime."""

import os
import socket
import threading
import time

import pytest

from macbot.config import Settings, load, prepare, save
from macbot.orchestrator import MacBotOrchestrator
from macbot.runtime import Runtime

pytestmark = pytest.mark.models


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    root = load().data_dir
    settings = Settings(data_dir=tmp_path_factory.mktemp("real-runtime"))
    prepare(settings)
    for name in [
        "qwen3.5-2b-official",
        "parakeet",
        "qwen3-tts-1.7b",
        "silero",
        "minilm",
    ]:
        assert (root / "models" / name).is_dir(), f"Provision required real model {name}"
        (settings.data_dir / "models" / name).symlink_to(
            root / "models" / name, target_is_directory=True
        )
    assert (root / "bin/llama-server").is_file(), "Build inference binaries before testing"
    (settings.data_dir / "bin").symlink_to(root / "bin", target_is_directory=True)
    settings.models.llm = "qwen3.5-2b-official"
    settings.models.temperature = 0
    settings.models.llm_url = f"http://127.0.0.1:{port()}"
    for endpoint in [
        settings.services.rag,
        settings.services.assistant,
        settings.services.dashboard,
        settings.services.orchestrator,
    ]:
        endpoint.port = port()
    save(settings)
    supervisor = MacBotOrchestrator(settings)
    runtime = None
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"h" * 32)
    os.close(write_fd)
    os.environ["MACBOT_HISTORY_KEY_FD"] = str(read_fd)
    try:
        supervisor.definitions()
        for name in ["llm", "rag"]:
            result = supervisor.start_service(supervisor.service_definitions[name], retries=120)
            assert result["success"], result
        runtime = Runtime(settings)
        yield runtime
    finally:
        os.environ.pop("MACBOT_HISTORY_KEY_FD", None)
        if runtime:
            runtime.close()
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()


def until(engine, turn, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    cursor = 0
    events = []
    while time.monotonic() < deadline:
        data = engine.events.read(cursor, timeout=0.1)
        cursor = data["cursor"]
        events.extend(e for e in data["events"] if e["turn_id"] == turn.id)
        if any(predicate(e) for e in events):
            return events
    raise AssertionError(f"Turn did not reach expected state: {events}")


def completed(engine, text, *, session="test-session"):
    turn = engine.submit(text, speak=False, session_id=session)
    events = until(engine, turn, lambda e: e["state"] in {"completed", "failed"})
    assert events[-1]["state"] == "completed", events
    return events


def text(events):
    return "".join(e["data"].get("text", "") for e in events if e["kind"] == "delta")


def test_real_generation_is_durable_per_session_and_clear_is_scoped(engine):
    engine.clear("session-a")
    engine.clear("session-b")
    events = completed(engine, "What is two plus two? Answer with the number.", session="session-a")
    assert "4" in text(events)
    assert len(engine._history_for("session-a")) == 2
    completed(engine, "Reply with the word separate.", session="session-b")
    engine.clear("session-a")
    assert not engine._history_for("session-a")
    assert engine._history_for("session-b")


@pytest.mark.parametrize(
    "greeting", ["Hello, how are you?", "What else can you do for me?", "Well,"]
)
def test_ordinary_conversation_never_plans_or_runs_an_action(engine, greeting):
    engine.clear("conversation")
    events = completed(engine, greeting, session="conversation")
    assert text(events).strip()
    assert not any(e["kind"] in {"action", "tool", "tool_result", "approval"} for e in events)


def test_real_local_time_uses_clock_result_before_response_and_never_searches(engine):
    engine.clear("clock")
    events = completed(engine, "What time is it?", session="clock")
    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["data"]["tool"] == "local_time"
    assert results[0]["data"]["result"]["source"] == "mac_clock"
    assert not any(e["data"].get("tool") == "web_search" for e in events)
    first_result = results[0]["seq"]
    assert any(e["kind"] == "delta" and e["seq"] > first_result for e in events)


def test_document_only_request_cannot_invoke_external_search(engine):
    response = engine.tools.client.post(
        engine.settings.services.rag.url + "/api/documents",
        headers=engine.auth.headers("rag"),
        json={"title": "Local note", "content": "The project codename is cobalt lantern."},
    )
    response.raise_for_status()
    engine.clear("documents")
    events = completed(
        engine,
        "Search my documents for the project codename without searching the web.",
        session="documents",
    )
    tools = [e["data"].get("tool") for e in events if e["kind"] == "tool_result"]
    assert tools == ["rag_search"]
    assert "cobalt" in text(events).lower()


def test_compound_app_request_produces_two_grounded_actions_without_executing(engine):
    intent = engine.intent.route("Open Calculator and Notes", threading.Event())
    assert intent.mode == "act"
    assert [(action.name, action.arguments["app"]) for action in intent.actions] == [
        ("open_app", "Calculator"),
        ("open_app", "Notes"),
    ]
    assert all(action.source_span in "Open Calculator and Notes" for action in intent.actions)


def test_context_compacts_at_seventy_percent_and_preserves_recent_fact(engine):
    session = "compaction"
    engine.clear(session)
    original_limit = engine.settings.models.context_length
    engine.settings.models.context_length = 4096
    try:
        history = []
        for index in range(8):
            turn_id = f"old-{index}"
            pair = [
                {
                    "role": "user",
                    "content": f"Historical note {index}. " + "A bounded older detail. " * 80,
                },
                {"role": "assistant", "content": "Noted."},
            ]
            ids = engine.history_store.append_messages(session, turn_id, pair)
            for row_id, message in zip(ids, pair, strict=True):
                message.update(_id=row_id, _turn_id=turn_id)
            history.extend(pair)
        recent = [
            {
                "role": "user",
                "content": "My verification word is cobalt. Remember it for my next question.",
            },
            {"role": "assistant", "content": "Your verification word is cobalt."},
        ]
        ids = engine.history_store.append_messages(session, "recent", recent)
        for row_id, message in zip(ids, recent, strict=True):
            message.update(_id=row_id, _turn_id="recent")
        history.extend(recent)
        engine.histories[session] = history
        events = completed(
            engine,
            "What is my verification word? Answer only that word.",
            session=session,
        )
        assert "cobalt" in text(events).lower()
        compacted = [e for e in engine.events.read(0)["events"] if e["kind"] == "context_compacted"]
        assert compacted
        assert engine.status()["context"]["prompt_tokens"] + 256 <= 4096
    finally:
        engine.settings.models.context_length = original_limit
        engine.clear(session)


@pytest.mark.parametrize("attempt", range(10))
def test_interruption_discards_late_output_before_next_turn(engine, attempt):
    session = f"interrupt-{attempt}"
    engine.clear(session)
    turn = engine.submit(
        "Explain photosynthesis in detailed numbered steps.", speak=False, session_id=session
    )
    until(engine, turn, lambda e: e["kind"] == "delta")
    engine.interrupt()
    cutoff = engine.events.seq
    replacement = engine.submit(
        "What is the capital of France? Answer briefly.", speak=False, session_id=session
    )
    events = until(engine, replacement, lambda e: e["state"] in {"completed", "failed"})
    assert events[-1]["state"] == "completed"
    assert "Paris" in text(events)
    assert not any(
        e["turn_id"] == turn.id and e["kind"] == "delta"
        for e in engine.events.read(cutoff)["events"]
    )
    assert engine.turns.qsize() <= 4 and engine.speech.qsize() <= 4


@pytest.mark.device
def test_requested_app_executes_once_and_returns_actual_result(engine):
    import psutil

    session = "device-app"
    engine.clear(session)
    events = completed(engine, "Open Calculator.", session=session)
    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["data"]["tool"] == "open_app"
    assert results[0]["data"]["result"] == {"status": "completed", "app": "Calculator"}
    assert any(p.info["name"] == "Calculator" for p in psutil.process_iter(["name"]))
    assert not any(e["kind"] == "approval" for e in events)
    assert any(e["kind"] == "delta" and e["seq"] > results[0]["seq"] for e in events)

"""Actual llama inference and RAG services exercising the shared turn runtime."""

import json
import socket
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
    s = Settings(data_dir=tmp_path_factory.mktemp("real-runtime"))
    prepare(s)
    for name in ["qwen3.5-2b", "parakeet", "amy", "silero", "minilm"]:
        assert (root / "models" / name).is_dir(), f"Provision required real model {name}"
        (s.data_dir / "models" / name).symlink_to(root / "models" / name, target_is_directory=True)
    assert (root / "bin/llama-server").is_file(), "Build inference binaries before testing"
    (s.data_dir / "bin").symlink_to(root / "bin", target_is_directory=True)
    s.models.llm = "qwen3.5-2b"
    s.models.temperature = 0
    s.models.llm_url = f"http://127.0.0.1:{port()}"
    for endpoint in [
        s.services.rag,
        s.services.assistant,
        s.services.dashboard,
        s.services.orchestrator,
    ]:
        endpoint.port = port()
    save(s)
    supervisor = MacBotOrchestrator(s)
    runtime = None
    try:
        supervisor.definitions()
        for name in ["llm", "rag"]:
            result = supervisor.start_service(supervisor.service_definitions[name], retries=120)
            assert result["success"], result
        runtime = Runtime(s)
        yield runtime
    finally:
        if runtime:
            runtime.close()
        supervisor.stop_all()
        supervisor.client.close()
        supervisor.auth.close()


def until(engine, turn, predicate, timeout=20):
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


def test_real_generation_and_clear_no_deadlock(engine):
    engine.clear()
    turn = engine.submit(
        "What is two plus two? Answer with the number.", speak=False, session_id="test-session"
    )
    events = until(engine, turn, lambda e: e["state"] in {"completed", "failed"})
    assert events[-1]["state"] == "completed"

    assert "4" in "".join(e["data"]["text"] for e in events if e["kind"] == "delta")
    assert len(engine.history) == 2
    engine.clear()
    assert not engine.history


def test_context_prunes_whole_turns_and_preserves_recent_fact(engine):
    engine.clear()
    for i in range(8):
        engine.history.extend(
            [
                {
                    "role": "user",
                    "content": f"Earlier note {i}. " + "A long historical description. " * 180,
                },
                {"role": "assistant", "content": "Noted."},
            ]
        )
    engine.history.extend(
        [
            {
                "role": "user",
                "content": "My verification word is cobalt. Remember it for my next question.",
            },
            {"role": "assistant", "content": "Your verification word is cobalt."},
        ]
    )
    turn = engine.submit("What is my verification word? Answer only that word.", speak=False)
    events = until(engine, turn, lambda e: e["state"] in {"completed", "failed"})
    assert events[-1]["state"] == "completed"
    assert "cobalt" in "".join(e["data"]["text"] for e in events if e["kind"] == "delta").lower()
    context = engine.status()["context"]
    assert context["pruned_turns"] > 0
    assert context["prompt_tokens"] + context["reserved_output_tokens"] <= context["limit"]
    assert engine.history[0]["role"] == "user"
    assert engine.history[-1]["role"] == "assistant"
    engine.clear()
    assert not engine.history


def test_real_tool_proposal_requires_bound_dashboard_decision(engine):
    engine.clear()
    turn = engine.submit("Open Calculator.", speak=False, session_id="requesting-session")
    events = until(
        engine, turn, lambda e: e["state"] in {"approval_required", "failed", "completed"}
    )
    approvals = [e for e in events if e["kind"] == "approval"]
    assert approvals, events
    action = approvals[0]["data"]["action_id"]
    with pytest.raises(PermissionError):
        engine.decide(action, turn.id, True, "other-session")
    result = engine.decide(action, turn.id, False, "requesting-session")
    assert result["status"] == "denied"
    with pytest.raises(PermissionError):
        engine.decide(action, turn.id, True, "requesting-session")
    events = until(engine, turn, lambda e: e["state"] in {"completed", "failed"})
    assert events[-1]["state"] == "completed"

    # The next request must retain the actual denied result in the tool role,
    # not just the assistant's paraphrase or an elevated system instruction.
    results = [m for m in engine.history if m["role"] == "tool"]
    assert len(results) == 1
    assert json.loads(results[0]["content"])["status"] == "denied"
    calls = [c for m in engine.history for c in m.get("tool_calls", [])]
    assert results[0]["tool_call_id"] == calls[0]["id"]
    assert not any(m["role"] == "system" for m in engine.history)

    # An actual prior action/result must not authorize the next ordinary turn.
    for text in ["Hello, how are you?", "What else can you do for me?", "Well,"]:
        turn = engine.submit(text, speak=False)
        events = until(
            engine, turn, lambda e: e["state"] in {"completed", "failed", "approval_required"}
        )
        assert events[-1]["state"] == "completed", events
        assert not any(e["kind"] in {"approval", "tool", "tool_result"} for e in events)
        assert any(e["kind"] == "delta" and e["data"]["text"].strip() for e in events)


def test_real_local_time_uses_tool_result_without_external_search(engine):
    engine.clear()
    turn = engine.submit("What time is now?", speak=False)
    events = until(
        engine, turn, lambda e: e["state"] in {"completed", "failed", "approval_required"}
    )
    assert events[-1]["state"] == "completed", events
    results = [e["data"] for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1 and results[0]["tool"] == "local_time"
    assert results[0]["result"]["source"] == "mac_clock"
    assert any(e["kind"] == "delta" for e in events)


@pytest.mark.device
def test_requested_app_executes_once_and_returns_actual_result(engine):
    """Operator-visible test: launches Calculator, never a substitute command."""
    import psutil

    engine.clear()
    engine.settings.tools.auto_run_requested = True
    try:
        turn = engine.submit("Open Calculator.", speak=False)
        events = until(
            engine, turn, lambda e: e["state"] in {"completed", "failed", "approval_required"}
        )
        assert events[-1]["state"] == "completed", events
        results = [e["data"] for e in events if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert results[0] == {
            "tool": "open_app",
            "result": {"status": "completed", "app": "Calculator"},
        }
        assert any(p.info["name"] == "Calculator" for p in psutil.process_iter(["name"]))
        assert any(e["kind"] == "delta" for e in events)
        assert not any(e["kind"] == "approval" for e in events)
        assert not engine.tools.pending
        assert any(m["role"] == "tool" and '"completed"' in m["content"] for m in engine.history)
        result_seq = next(e["seq"] for e in events if e["kind"] == "tool_result")
        assert any(e["kind"] == "delta" and e["seq"] > result_seq for e in events)
        # The completed real action must not leak authority into small talk.
        turn = engine.submit("Hello, how are you?", speak=False)
        events = until(
            engine, turn, lambda e: e["state"] in {"completed", "failed", "approval_required"}
        )
        assert events[-1]["state"] == "completed", events
        assert not any(e["kind"] in {"approval", "tool", "tool_result"} for e in events)
    finally:
        engine.settings.tools.auto_run_requested = False


@pytest.mark.parametrize("attempt", range(20))
def test_interruption_discards_late_output_before_next_turn(engine, attempt):
    engine.clear()
    turn = engine.submit("Explain photosynthesis in detailed numbered steps.", speak=False)
    until(engine, turn, lambda e: e["kind"] == "delta")
    engine.interrupt()
    cutoff = engine.events.seq
    replacement = engine.submit("What is the capital of France? Answer briefly.", speak=False)
    events = until(engine, replacement, lambda e: e["state"] in {"completed", "failed"})
    assert events[-1]["state"] == "completed"
    assert "Paris" in "".join(e["data"]["text"] for e in events if e["kind"] == "delta")
    assert not any(
        e["turn_id"] == turn.id and e["kind"] == "delta"
        for e in engine.events.read(cutoff)["events"]
    )
    assert engine.turns.qsize() <= 4 and engine.speech.qsize() <= 4

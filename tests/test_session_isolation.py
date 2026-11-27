import numpy as np

from macbot.config import Settings, prepare
from macbot.events import EventJournal
from macbot.runtime import Runtime, Turn


def test_event_reads_are_scoped_to_authenticated_session(tmp_path):
    settings = Settings(data_dir=tmp_path, privacy={"history_enabled": False})
    prepare(settings)
    runtime = Runtime(settings, load_speech=False)
    runtime.events.publish("session-a", "a", "running", "user", text="private-a")
    runtime.events.publish("session-b", "b", "running", "user", text="private-b")
    try:
        response = runtime.events.read(0, timeout=0, session_id="session-a")
        assert [event["session_id"] for event in response["events"]] == ["session-a"]
        assert response["events"][0]["data"]["text"] == "private-a"
    finally:
        runtime.close()


def test_clear_uses_explicit_session_and_preserves_other_sessions(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    runtime = Runtime(settings, load_speech=False)
    runtime.histories["session-a"] = [{"role": "user", "content": "private-a"}]
    runtime.histories["session-b"] = [{"role": "user", "content": "private-b"}]
    try:
        runtime.clear(session_id="session-a")
        assert runtime._history_for("session-a") == []
        assert runtime._history_for("session-b")[0]["content"] == "private-b"
    finally:
        runtime.close()


def test_dictation_delivers_private_draft_without_starting_a_conversation(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, privacy={"history_enabled": False})
    prepare(settings)
    runtime = Runtime(settings, load_speech=False)
    persisted = []
    runtime.events.sink = lambda _, event: persisted.append(event)
    monkeypatch.setattr(runtime, "transcriber", object())
    monkeypatch.setattr(runtime, "_transcribe_final", lambda _turn: " Text for my draft. ")
    turn = Turn("draft", "native", None, False, audio=np.zeros(512), dictation_only=True)
    try:
        runtime._process(turn)
        events = runtime.events.read(0, session_id="native")["events"]
        assert turn.terminal
        assert events[-1]["kind"] == "dictation"
        assert events[-1]["data"]["text"] == "Text for my draft."
        assert all(event["kind"] != "dictation" for event in persisted)
        assert runtime._history_for("native") == []
        assert runtime.speech.empty()
    finally:
        runtime.transcriber = None
        runtime.close()


def test_assistant_http_has_no_operator_control_plane(tmp_path):
    from macbot.voice_assistant import create_app

    settings = Settings(data_dir=tmp_path, privacy={"history_enabled": False})
    prepare(settings)
    runtime = Runtime(settings, load_speech=False)
    try:
        routes = {rule.rule for rule in create_app(settings, runtime).url_map.iter_rules()}
        assert routes == {"/static/<path:filename>", "/health", "/ready", "/info"}
    finally:
        runtime.close()


def test_streaming_deltas_are_not_written_to_event_history(tmp_path):
    persisted = []
    journal = EventJournal(sink=lambda epoch, event: persisted.append((epoch, event)))
    journal.publish("native", "turn", "running", "delta", text="token")
    journal.publish("native", "turn", "completed", "state")
    assert len(persisted) == 1
    assert persisted[0][1]["state"] == "completed"

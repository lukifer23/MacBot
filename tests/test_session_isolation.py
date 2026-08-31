from macbot.config import Settings, prepare
from macbot.events import EventJournal
from macbot.runtime import Runtime


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

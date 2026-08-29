import os

import numpy as np

from macbot.config import Settings, prepare
from macbot.history import HistoryStore


def test_history_is_encrypted_and_round_trips(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    store = HistoryStore(settings, os.urandom(32))
    secret = "cobalt-private-history-marker"
    try:
        ids = store.append_messages("session-a", "turn-a", [{"role": "user", "content": secret}])
        assert ids
        restored = store.load_messages("session-a")
        assert len(restored) == 1
        assert restored[0]["role"] == "user" and restored[0]["content"] == secret
        assert restored[0]["_turn_id"] == "turn-a"
        assert secret.encode() not in (tmp_path / "history.sqlite3").read_bytes()
        store.clear_session("session-a")
        assert store.load_messages("session-a") == []
    finally:
        store.close()


def test_wrong_history_key_fails_closed(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    first = HistoryStore(settings, b"a" * 32)
    first.append_messages("s", "t", [{"role": "user", "content": "private"}])
    first.close()
    second = HistoryStore(settings, b"b" * 32)
    try:
        try:
            second.load_messages("s")
        except Exception as exc:
            assert "private" not in str(exc)
        else:
            raise AssertionError("History decrypted with the wrong key")
    finally:
        second.close()


def test_source_linked_summary_hides_compacted_messages_and_retrieves_by_vector(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    store = HistoryStore(settings, b"s" * 32)
    store.append_messages(
        "session",
        "turn-one",
        [{"role": "user", "content": "My favorite color is cobalt."}],
    )
    store.append_messages(
        "session",
        "turn-two",
        [{"role": "user", "content": "Keep this active."}],
    )
    vector = np.zeros(384, dtype=np.float32)
    vector[12] = 1
    store.save_summary(
        "session",
        ["turn-one"],
        "[turn:turn-one] The user's favorite color is cobalt.",
        vector,
    )
    assert [item["_turn_id"] for item in store.load_messages("session")] == ["turn-two"]
    results = store.search_summaries("session", vector)
    assert results[0]["source_turn_ids"] == ["turn-one"]
    assert results[0]["score"] == 1
    store.close()

import os

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
        assert store.load_messages("session-a") == [{"role": "user", "content": secret}]
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

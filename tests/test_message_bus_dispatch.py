"""The bounded event journal replaces thread-per-event message buses."""

import threading
from concurrent.futures import ThreadPoolExecutor

from macbot.events import EventJournal


def test_bounded_delivery_reports_reconnect_gap():
    journal = EventJournal(capacity=3)
    for i in range(5):
        journal.publish("s", "turn", "running", value=i)
    data = journal.read(after=1)
    assert data["gap"]
    assert [e["seq"] for e in data["events"]] == [3, 4, 5]
    assert data["cursor"] == 5
    assert all(e["session_id"] == "s" and e["turn_id"] == "turn" for e in data["events"])
    assert journal.read(after=5)["events"] == []


def test_concurrent_publish_is_ordered_and_close_wakes_reader():
    journal = EventJournal(capacity=100)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: journal.publish("s", str(n), "completed"), range(80)))
    assert [e["seq"] for e in journal.read(0)["events"]] == list(range(1, 81))
    result = []
    thread = threading.Thread(target=lambda: result.append(journal.read(80, timeout=10)))
    thread.start()
    journal.close()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert result[0]["events"] == []


def test_restarted_journal_resets_cursor_even_when_empty():
    first = EventJournal()
    first.publish("s", "old", "completed")
    old = first.read(0)
    restarted = EventJournal()
    empty = restarted.read(old["cursor"], timeout=10, epoch=old["epoch"])
    assert empty["reset"] and empty["cursor"] == 0
    assert empty["epoch"] != old["epoch"]
    restarted.publish("s", "new", "running")
    result = restarted.read(20, epoch=old["epoch"])
    assert result["reset"] and result["events"][0]["turn_id"] == "new"
    assert not restarted.read(1, epoch=result["epoch"])["reset"]


def test_session_filter_does_not_report_other_sessions_as_a_gap():
    journal = EventJournal(capacity=10)
    journal.publish("native", "one", "completed")
    journal.publish("local", "hidden", "completed")
    journal.publish("native", "two", "completed")
    result = journal.read(1, session_id="native")
    assert not result["gap"]
    assert [event["turn_id"] for event in result["events"]] == ["two"]


def test_partial_transcription_is_live_only():
    persisted = []
    journal = EventJournal(sink=lambda epoch, event: persisted.append((epoch, event)))
    event = journal.publish(
        "native", "capture", "running", "transcription", text="partial", partial=True
    )
    assert event.data["partial"] is True
    assert journal.read(0)["events"][0]["data"]["text"] == "partial"
    assert persisted == []

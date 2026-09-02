import threading
import time

from macbot.events import EventJournal
from macbot.inference import InferenceLane, inference_profiles
from macbot.runtime import Runtime, Turn


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached")


def test_foreground_request_runs_before_queued_background_request():
    lane = InferenceLane()
    blocker = lane.acquire(request_id="active", kind="model")
    assert blocker is not None
    order = []

    def run(request_id, kind):
        request = lane.acquire(request_id=request_id, kind=kind)
        assert request is not None
        with request:
            order.append(request_id)

    background = threading.Thread(target=run, args=("background", "background"))
    foreground = threading.Thread(target=run, args=("foreground", "foreground"))
    background.start()
    _wait_until(lambda: "background" in lane._requests)
    foreground.start()
    _wait_until(lambda: "foreground" in lane._requests)
    blocker.__exit__()
    background.join(timeout=1)
    foreground.join(timeout=1)
    assert not background.is_alive() and not foreground.is_alive()
    assert order == ["foreground", "background"]


def test_cancel_targets_only_the_identified_request():
    lane = InferenceLane()
    active = lane.acquire(request_id="active", kind="model")
    assert active is not None
    active_cancelled = threading.Event()
    active.bind_active_cancel(active_cancelled.set)
    queued_result = []

    def queue_second():
        queued_result.append(lane.acquire(request_id="queued", kind="model"))

    queued = threading.Thread(target=queue_second)
    queued.start()
    _wait_until(lambda: "queued" in lane._requests)
    assert lane.cancel("queued")
    queued.join(timeout=1)
    assert queued_result == [None]
    assert not active_cancelled.is_set()
    assert lane.cancel("active")
    assert active_cancelled.wait(1)
    active.__exit__()


def test_named_inference_profiles_share_one_lane_with_distinct_budgets():
    profiles = inference_profiles(256, 0.1)
    assert set(profiles) == {"conversation", "task_plan", "task_final", "compaction", "model"}
    assert profiles["conversation"].max_tokens == 256
    assert profiles["task_plan"].max_tokens == 512
    assert profiles["task_final"].max_tokens == 768
    assert profiles["compaction"].temperature == 0.0


def test_runtime_coalesces_fast_token_stream_without_losing_text():
    runtime = Runtime.__new__(Runtime)
    runtime.lock = threading.RLock()
    runtime.error_count = 0
    runtime.last_error = None
    runtime.events = EventJournal()
    turn = Turn("turn", "native", "hello", False)
    expected = "".join(str(index) for index in range(100))
    for index in range(100):
        runtime._emit_delta(turn, str(index))
    runtime._emit_delta(turn, force=True)
    events = runtime.events.read(0, session_id="native")["events"]
    deltas = [event for event in events if event["kind"] == "delta"]
    assert len(deltas) <= 2
    assert "".join(event["data"]["text"] for event in deltas) == expected

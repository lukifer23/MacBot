import time
from macbot.message_bus import MessageBus


def test_last_seen_updates_on_message_exchange():
    bus = MessageBus()
    bus.start()
    bus.register_client("client1", "service")
    bus.register_client("client2", "service")

    initial_c1 = bus.clients["client1"]["last_seen"]
    initial_c2 = bus.clients["client2"]["last_seen"]

    time.sleep(0.02)
    bus.send_message({"type": "ping"}, target_client="client1")
    after_c1 = bus.clients["client1"]["last_seen"]
    after_c2 = bus.clients["client2"]["last_seen"]
    assert after_c1 > initial_c1
    assert after_c2 == initial_c2

    status = bus.get_service_status()
    assert status["service"]["clients"]["client1"]["last_seen"] == after_c1

    time.sleep(0.02)
    bus.broadcast({"type": "all"})
    final_c1 = bus.clients["client1"]["last_seen"]
    final_c2 = bus.clients["client2"]["last_seen"]
    assert final_c1 > after_c1
    assert final_c2 > initial_c2
    assert status["service"]["last_seen"] <= bus.get_service_status()["service"]["last_seen"]

    bus.stop()

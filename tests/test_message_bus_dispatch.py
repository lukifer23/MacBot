from macbot.message_bus import MessageBus


def test_publish_to_specific_client():
    bus = MessageBus()
    bus.start()
    try:
        client_queue = bus.register_client("client1", "serviceA")
        bus.publish({"type": "test", "content": 123}, target_client="client1")
        message = client_queue.get(timeout=1)
        assert message["content"] == 123
    finally:
        bus.stop()


def test_publish_to_service_type():
    bus = MessageBus()
    bus.start()
    try:
        q1 = bus.register_client("client1", "serviceA")
        q2 = bus.register_client("client2", "serviceA")
        bus.publish({"type": "broadcast", "value": "hi"}, target_service="serviceA")
        m1 = q1.get(timeout=1)
        m2 = q2.get(timeout=1)
        assert m1["value"] == "hi"
        assert m2["value"] == "hi"
    finally:
        bus.stop()

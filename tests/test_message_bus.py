#!/usr/bin/env python3
"""Integration tests for the network based message bus."""

from __future__ import annotations

import os
import sys
import time
from multiprocessing import Process, Queue

# Ensure the src directory is importable when tests are run directly
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from macbot.message_bus import start_message_bus, stop_message_bus
from macbot.message_bus_client import MessageBusClient


def _client_process(
    host: str,
    port: int,
    service_type: str,
    recv_q: Queue | None = None,
    send: str | None = None,
    target_service: str | None = None,
    broadcast: bool = False,
) -> None:
    """Helper process that optionally sends and/or receives a message."""

    client = MessageBusClient(host=host, port=port, service_type=service_type)
    if recv_q is not None:
        def handler(msg: dict) -> None:
            recv_q.put(msg["content"])

        client.register_handler("test", handler)

    client.start()
    time.sleep(0.5)  # allow connection establishment

    if send is not None:
        client.send_message(
            {"type": "test", "content": send},
            target_service=target_service,
            broadcast=broadcast,
        )

    time.sleep(1)
    client.stop()


def test_targeted_message() -> None:
    """Messages addressed to a service type are delivered to that service."""
    bus = start_message_bus(port=0)
    port = bus.port

    recv = Queue()
    receiver = Process(
        target=_client_process, args=("localhost", port, "receiver", recv)
    )
    receiver.start()
    time.sleep(0.5)

    sender = Process(
        target=_client_process,
        args=("localhost", port, "sender", None, "hello", "receiver"),
    )
    sender.start()

    assert recv.get(timeout=5) == "hello"

    sender.join()
    receiver.join()
    stop_message_bus()


def test_broadcast_message() -> None:
    """Broadcast messages are delivered to all connected clients."""
    bus = start_message_bus(port=0)
    port = bus.port

    q1 = Queue()
    q2 = Queue()

    client1 = Process(
        target=_client_process, args=("localhost", port, "c1", q1)
    )
    client2 = Process(
        target=_client_process, args=("localhost", port, "c2", q2)
    )
    client1.start()
    client2.start()
    time.sleep(0.5)

    sender = Process(
        target=_client_process,
        args=("localhost", port, "sender", None, "hi", None, True),
    )
    sender.start()

    assert q1.get(timeout=5) == "hi"
    assert q2.get(timeout=5) == "hi"

    sender.join()
    client1.join()
    client2.join()
    stop_message_bus()


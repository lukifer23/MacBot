"""Tests for MessageBusClient network resilience."""

import threading
import time
from typing import Callable, Dict, List

from websockets.sync.server import serve

from macbot.message_bus_client import MessageBusClient


class TestServer:
    """Simple broadcast WebSocket server using synchronous API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.clients: List = []
        self.thread: threading.Thread | None = None
        self.server = None

    def _handler(self, websocket):
        self.clients.append(websocket)
        try:
            for message in websocket:
                for ws in list(self.clients):
                    try:
                        ws.send(message)
                    except Exception:
                        pass
        finally:
            if websocket in self.clients:
                self.clients.remove(websocket)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        time.sleep(0.1)

    def _run(self) -> None:
        with serve(self._handler, self.host, self.port) as server:
            self.server = server
            server.serve_forever()

    def stop(self) -> None:
        self.drop_connections()
        if self.server:
            self.server.shutdown()
        if self.thread:
            self.thread.join()
        self.thread = None
        self.server = None
        self.clients = []

    def drop_connections(self) -> None:
        for ws in list(self.clients):
            try:
                ws.close()
            except Exception:
                pass


def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(0.1)
    return False


def create_client(port: int, events: List[str], messages: List[Dict]) -> MessageBusClient:
    client = MessageBusClient(
        host="127.0.0.1",
        port=port,
        service_type="test",
        heartbeat_interval=0.5,
        heartbeat_timeout=0.5,
        reconnect_initial=0.1,
        reconnect_max=1.0,
        on_disconnect=lambda: events.append("disconnected"),
        on_reconnect=lambda: events.append("reconnected"),
    )
    client.register_handler("test", lambda m: messages.append(m))
    return client


def test_reconnect_on_server_restart():
    server = TestServer(port=8765)
    server.start()

    events: List[str] = []
    messages: List[Dict] = []
    client = create_client(8765, events, messages)
    client.start()

    assert wait_for(lambda: client.is_connected())
    client.send_message({"type": "test", "content": "hello"})
    assert wait_for(lambda: len(messages) == 1)

    server.stop()
    assert wait_for(lambda: "disconnected" in events)

    server.start()
    assert wait_for(lambda: events.count("reconnected") >= 1)

    client.send_message({"type": "test", "content": "again"})
    assert wait_for(lambda: len(messages) == 2)

    client.stop()
    server.stop()


def test_reconnect_on_network_drop():
    server = TestServer(port=8766)
    server.start()

    events: List[str] = []
    messages: List[Dict] = []
    client = create_client(8766, events, messages)
    client.start()

    assert wait_for(client.is_connected)
    client.send_message({"type": "test", "content": "hello"})
    assert wait_for(lambda: len(messages) == 1)

    server.drop_connections()
    assert wait_for(lambda: "disconnected" in events)
    assert wait_for(lambda: events.count("reconnected") >= 1)

    client.send_message({"type": "test", "content": "hello again"})
    assert wait_for(lambda: len(messages) == 2)

    client.stop()
    server.stop()


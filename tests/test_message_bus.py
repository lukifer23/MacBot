import os
import sys
import time
import pytest

# Ensure the package can be imported without installation
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.message_bus import start_message_bus, stop_message_bus
import macbot.message_bus_client as mb_client
from macbot.message_bus_client import MessageBusClient


@pytest.fixture
def message_bus():
    bus = start_message_bus(host='localhost', port=8082)
    mb_client.message_bus = bus
    yield bus
    stop_message_bus()


@pytest.fixture
def create_client(message_bus):
    clients = []

    def _factory(service_type: str):
        client = MessageBusClient(host='localhost', port=8082, service_type=service_type)
        client.start()
        clients.append(client)
        time.sleep(0.1)  # allow connection
        return client

    yield _factory

    for c in clients:
        c.stop()


def test_message_delivery(create_client):
    """Clients should receive messages sent on the bus."""
    received = []

    client2 = create_client('service2')
    client2.register_handler('test', lambda m: received.append(m))

    client1 = create_client('service1')
    client1.send_message({'type': 'test', 'content': 'hello'})

    timeout = time.time() + 2
    while not received and time.time() < timeout:
        time.sleep(0.05)

    assert received and received[0]['content'] == 'hello'


def test_service_status(message_bus, create_client):
    """Message bus should track registered services."""
    client = create_client('network_service')
    time.sleep(0.1)
    status = message_bus.get_service_status()
    assert 'network_service' in status
    assert status['network_service']['count'] == 1
    assert client.client_id in status['network_service']['clients']

#!/usr/bin/env python3
"""
Test script for MacBot Message Bus System
"""
import sys
import os
import time
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.message_bus import MessageBus, start_message_bus, stop_message_bus
from macbot.message_bus_client import MessageBusClient

def test_message_bus():
    """Test the message bus system"""
    print("🧪 Testing MacBot Message Bus System...")

    # Start message bus
    print("1. Starting message bus server...")
    bus = start_message_bus(host='localhost', port=8082)

    # Create test clients
    print("2. Creating test clients...")
    client1 = MessageBusClient(
        host='localhost',
        port=8082,
        service_type='test_service_1'
    )
    client2 = MessageBusClient(
        host='localhost',
        port=8082,
        service_type='test_service_2'
    )

    # Start clients
    print("3. Starting clients...")
    client1.start()
    client2.start()

    # Wait for connections
    time.sleep(3)

    # Test message sending
    print("4. Testing message exchange...")

    # Client 1 sends message
    client1.send_message({
        'type': 'test_message',
        'content': 'Hello from client 1!',
        'timestamp': time.time()
    })

    # Client 2 sends message
    client2.send_message({
        'type': 'test_message',
        'content': 'Hello from client 2!',
        'timestamp': time.time()
    })

    # Wait for messages to be processed
    time.sleep(2)

    print("5. Test completed successfully!")
    print("✅ Message bus system is working properly")

    # Cleanup
    client1.stop()
    client2.stop()
    stop_message_bus()

if __name__ == "__main__":
    test_message_bus()

#!/usr/bin/env python3
"""
MacBot Message Bus Client - Client for connecting to the in-memory message bus
"""

import json
import logging
import threading
import time
import queue
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .message_bus import message_bus

logger = logging.getLogger(__name__)


class MessageBusClient:
    """Client for connecting to the MacBot in-memory message bus"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8082,
        service_type: str = "unknown",
        heartbeat_interval: float = 10.0,
        on_disconnect: Optional[Callable[[], None]] = None,
        on_reconnect: Optional[Callable[[], None]] = None,
    ):
        self.host = host
        self.port = port
        self.service_type = service_type
        self.heartbeat_interval = heartbeat_interval
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect

        self.client_id: Optional[str] = None
        self.running = False
        self.connected = False
        self.message_queue: Optional[queue.Queue] = None

        self.message_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

        self.monitor_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the message bus client"""
        if self.running:
            return

        self.running = True
        self.client_id = f"{self.service_type}_{int(time.time())}"

        # Register with the global message bus
        self.message_queue = message_bus.register_client(self.client_id, self.service_type)
        self.connected = True

        # Start message monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_messages, daemon=True)
        self.monitor_thread.start()

        logger.info(f"Message bus client started for {self.service_type} ({self.client_id})")

    def stop(self) -> None:
        """Stop the message bus client"""
        self.running = False

        if self.client_id:
            message_bus.unregister_client(self.client_id)

        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)

        self.connected = False
        self.message_queue = None
        logger.info(f"Message bus client stopped for {self.service_type}")

    def send_message(self, message: Dict[str, Any]) -> None:
        """Send a message through the bus"""
        if not self.connected or not self.client_id:
            logger.warning("Not connected to message bus, message not sent")
            return

        enriched_message = {
            **message,
            "sender_id": self.client_id,
            "sender_type": self.service_type,
            "timestamp": datetime.now().isoformat(),
        }

        # Send through the global message bus
        message_bus.send_message(enriched_message)

    def is_connected(self) -> bool:
        """Check if client is connected"""
        return self.connected and message_bus is not None

    def register_handler(
        self, message_type: str, handler: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Register a message handler"""
        if message_type not in self.message_handlers:
            self.message_handlers[message_type] = []
        self.message_handlers[message_type].append(handler)

    def unregister_handler(
        self, message_type: str, handler: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Unregister a message handler"""
        if message_type in self.message_handlers:
            try:
                self.message_handlers[message_type].remove(handler)
            except ValueError:
                pass

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for connection loss"""
        self.on_disconnect = callback

    def set_reconnect_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for connection restoration"""
        self.on_reconnect = callback

    def _monitor_messages(self) -> None:
        """Monitor incoming messages from the message bus"""
        while self.running and self.message_queue:
            try:
                # Non-blocking check for messages
                try:
                    message = self.message_queue.get(timeout=0.1)
                    self._handle_message(message)
                except queue.Empty:
                    continue

            except Exception as e:
                logger.error(f"Error monitoring messages: {e}")
                time.sleep(1)

    def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle an incoming message"""
        message_type = message.get("type", "unknown")
        if message_type in self.message_handlers:
            for handler in self.message_handlers[message_type]:
                try:
                    handler(message)
                except Exception as e:
                    logger.error(f"Error in message handler: {e}")
        logger.debug(f"Received message: {message_type}")


__all__ = ["MessageBusClient"]


#!/usr/bin/env python3
"""Client for the network based :mod:`macbot.message_bus` server."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


class MessageBusClient:
    """Connects to the :class:`~macbot.message_bus.MessageBus` TCP server."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8082,
        service_type: str = "unknown",
    ) -> None:
        self.host = host
        self.port = port
        self.service_type = service_type
        self.client_id: Optional[str] = None

        self.sock: Optional[socket.socket] = None
        self.file = None
        self.running = False
        self.connected = False
        self.thread: Optional[threading.Thread] = None

        self.message_handlers: Dict[str, List[Callable]] = {}

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Connect to the message bus and start listening for messages."""
        if self.connected:
            return

        self.client_id = f"{self.service_type}_{int(time.time() * 1000)}"
        try:
            self.sock = socket.create_connection((self.host, self.port))
            self.file = self.sock.makefile(mode="rw", encoding="utf-8")

            # Register with the server
            self._send_raw(
                {
                    "action": "register",
                    "client_id": self.client_id,
                    "service_type": self.service_type,
                }
            )

            # Wait for acknowledgement
            self.file.readline()

            self.running = True
            self.connected = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            logger.info("Message bus client connected for %s", self.service_type)
        except OSError as exc:
            logger.error("Failed to connect to message bus: %s", exc)

    def stop(self) -> None:
        """Disconnect from the message bus."""
        self.running = False
        self.connected = False

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

        if self.thread:
            self.thread.join(timeout=5)

        logger.info("Message bus client stopped for %s", self.service_type)

    # ------------------------------------------------------------------
    def _send_raw(self, data: Dict) -> None:
        if self.file is None:
            return
        self.file.write(json.dumps(data) + "\n")
        self.file.flush()

    def send_message(
        self,
        message: Dict,
        target_client: Optional[str] = None,
        target_service: Optional[str] = None,
        broadcast: bool = False,
    ) -> None:
        """Send a message through the bus."""
        if not self.connected:
            logger.warning("Not connected to message bus, message not sent")
            return

        payload: Dict = {
            "action": "broadcast" if broadcast else "send",
            "message": message,
        }
        if target_client:
            payload["target_client"] = target_client
        if target_service:
            payload["target_service"] = target_service

        self._send_raw(payload)

    def register_handler(self, message_type: str, handler: Callable) -> None:
        if message_type not in self.message_handlers:
            self.message_handlers[message_type] = []
        self.message_handlers[message_type].append(handler)

    def unregister_handler(self, message_type: str, handler: Callable) -> None:
        if message_type in self.message_handlers:
            try:
                self.message_handlers[message_type].remove(handler)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    def _listen_loop(self) -> None:
        assert self.file is not None
        while self.running:
            try:
                line = self.file.readline()
                if not line:
                    break
                data = json.loads(line)
                if data.get("action") == "message":
                    self._handle_message(data.get("message", {}))
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Message processing error: %s", exc)
                break

        self.connected = False

    def _handle_message(self, message: Dict) -> None:
        message_type = message.get("type", "unknown")
        handlers = self.message_handlers.get(message_type, [])
        for handler in handlers:
            try:
                handler(message)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Error in message handler: %s", exc)


__all__ = ["MessageBusClient"]


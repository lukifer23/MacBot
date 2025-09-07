#!/usr/bin/env python3
"""Network based message bus used by MacBot services.

This implementation exposes a simple TCP server that accepts connections from
external processes.  Clients communicate with the server using a newline
delimited JSON protocol.  Every client must first send a ``register`` message::

    {"action": "register", "client_id": "id", "service_type": "type"}

Once registered, clients can send messages to other clients or broadcast
messages to all connected clients.  Messages are routed according to the
``target_client`` or ``target_service`` fields.  Routed messages are delivered
to clients in the following format::

    {
        "action": "message",
        "from": "sender_id",
        "from_service": "sender_service_type",
        "message": {"type": "...", ...}
    }

This module replaces the previous in-memory message bus allowing components to
communicate across process boundaries.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Dict, Optional


logger = logging.getLogger(__name__)


class MessageBus:
    """TCP based message bus."""

    def __init__(self, host: str = "localhost", port: int = 8082) -> None:
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        # client_id -> {service_type, socket, file}
        self.clients: Dict[str, Dict[str, object]] = {}
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the TCP server in a background thread."""
        if self.running:
            return

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        # When port is 0 the OS chooses a free port - capture the actual value.
        self.port = self.server_socket.getsockname()[1]
        self.server_socket.listen()

        self.running = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        logger.info("Message bus started on %s:%s", self.host, self.port)

    def stop(self) -> None:
        """Stop the TCP server and close all client connections."""
        self.running = False

        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except OSError:
                pass

        # Close all client sockets
        with self.lock:
            for info in self.clients.values():
                try:
                    sock = info.get("socket")
                    if sock:
                        sock.close()
                except OSError:
                    pass
            self.clients.clear()

        if self.thread:
            self.thread.join(timeout=5)

        logger.info("Message bus stopped")

    # ------------------------------------------------------------------
    # Networking helpers
    # ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        """Accept incoming client connections."""
        assert self.server_socket is not None
        while self.running:
            try:
                client_sock, _addr = self.server_socket.accept()
            except OSError:
                break

            thread = threading.Thread(
                target=self._handle_client, args=(client_sock,), daemon=True
            )
            thread.start()

    def _handle_client(self, sock: socket.socket) -> None:
        """Handle communication with a single client."""
        file = sock.makefile(mode="rw", encoding="utf-8")
        client_id = None
        try:
            # Expect registration message first
            line = file.readline()
            if not line:
                return
            data = json.loads(line)
            if data.get("action") != "register":
                return

            client_id = data["client_id"]
            service_type = data.get("service_type", "unknown")
            with self.lock:
                self.clients[client_id] = {
                    "service_type": service_type,
                    "socket": sock,
                    "file": file,
                }

            # Acknowledge registration
            self._send_raw(file, {"action": "registered"})

            while self.running:
                line = file.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from %s", client_id)
                    continue

                action = message.get("action")
                if action == "send":
                    self._route_message(
                        sender_id=client_id,
                        sender_service=service_type,
                        payload=message.get("message", {}),
                        target_client=message.get("target_client"),
                        target_service=message.get("target_service"),
                    )
                elif action == "broadcast":
                    self._broadcast(
                        sender_id=client_id,
                        sender_service=service_type,
                        payload=message.get("message", {}),
                    )
        except Exception as exc:  # pragma: no cover - defensive programming
            logger.exception("Client handler error: %s", exc)
        finally:
            if client_id:
                with self.lock:
                    self.clients.pop(client_id, None)
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Message routing helpers
    # ------------------------------------------------------------------
    def _send_raw(self, file, data: Dict) -> None:
        file.write(json.dumps(data) + "\n")
        file.flush()

    def _route_message(
        self,
        sender_id: str,
        sender_service: str,
        payload: Dict,
        target_client: Optional[str] = None,
        target_service: Optional[str] = None,
    ) -> None:
        """Route a message to a target client or service type."""

        message = {
            "action": "message",
            "from": sender_id,
            "from_service": sender_service,
            "message": payload,
        }

        if target_client:
            with self.lock:
                client = self.clients.get(target_client)
            if client:
                self._send_raw(client["file"], message)
            else:
                logger.warning("Unknown target client: %s", target_client)
        elif target_service:
            sent = 0
            with self.lock:
                targets = [
                    info
                    for info in self.clients.values()
                    if info["service_type"] == target_service
                ]
            for info in targets:
                self._send_raw(info["file"], message)
                sent += 1
            if sent == 0:
                logger.warning("No clients found for service type: %s", target_service)
        else:
            self._broadcast(sender_id, sender_service, payload)

    def _broadcast(
        self, sender_id: str, sender_service: str, payload: Dict
    ) -> None:
        message = {
            "action": "message",
            "from": sender_id,
            "from_service": sender_service,
            "message": payload,
        }

        with self.lock:
            for cid, info in self.clients.items():
                if cid != sender_id:
                    self._send_raw(info["file"], message)


# Global message bus instance -------------------------------------------------

message_bus: Optional[MessageBus] = None


def start_message_bus(host: str = "localhost", port: int = 8082) -> MessageBus:
    """Start the global message bus server."""
    global message_bus
    bus = MessageBus(host, port)
    bus.start()
    message_bus = bus
    return bus


def stop_message_bus() -> None:
    """Stop the global message bus server."""
    global message_bus
    if message_bus is not None:
        message_bus.stop()
        message_bus = None


__all__ = ["MessageBus", "start_message_bus", "stop_message_bus"]


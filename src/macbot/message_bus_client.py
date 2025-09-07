#!/usr/bin/env python3
"""
MacBot Message Bus Client - Network-based client for connecting to the message bus
"""

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)


class MessageBusClient:
    """Client for connecting to the MacBot message bus over WebSockets"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8082,
        service_type: str = "unknown",
        heartbeat_interval: float = 10.0,
        heartbeat_timeout: float = 5.0,
        reconnect_initial: float = 1.0,
        reconnect_max: float = 30.0,
        on_disconnect: Optional[Callable[[], None]] = None,
        on_reconnect: Optional[Callable[[], None]] = None,
    ):
        self.host = host
        self.port = port
        self.service_type = service_type
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect

        self.client_id: Optional[str] = None
        self.running = False
        self.connected = False
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

        self.message_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the message bus client"""
        if self.running:
            return
        self.running = True
        self.client_id = f"{self.service_type}_{int(time.time())}"
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Stop the message bus client"""
        self.running = False
        if self.loop:
            if self.ws:
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=5)
        self.connected = False
        logger.info(f"Message bus client stopped for {self.service_type}")

    def send_message(self, message: Dict[str, Any]) -> None:
        """Send a message through the bus"""
        if not self.connected or not self.loop or not self.ws:
            logger.warning("Not connected to message bus, message not sent")
            return

        enriched_message = {
            **message,
            "sender_id": self.client_id,
            "sender_type": self.service_type,
            "timestamp": datetime.now().isoformat(),
        }

        async def _send():
            await self.ws.send(json.dumps(enriched_message))

        asyncio.run_coroutine_threadsafe(_send(), self.loop)

    def is_connected(self) -> bool:
        """Check if client is connected"""
        return self.connected

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

    def _run_loop(self) -> None:
        assert self.loop is not None
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connection_loop())

    async def _connection_loop(self) -> None:
        backoff = self.reconnect_initial
        has_connected_once = False
        while self.running:
            try:
                uri = f"ws://{self.host}:{self.port}"
                logger.info(f"Connecting to message bus at {uri}")
                async with websockets.connect(uri) as ws:
                    self.ws = ws
                    self.connected = True
                    if has_connected_once and self.on_reconnect:
                        self.on_reconnect()
                    has_connected_once = True
                    backoff = self.reconnect_initial
                    await self._manage_connection(ws)
            except Exception as e:  # pragma: no cover - network errors vary
                if self.connected:
                    self.connected = False
                    self.ws = None
                    if self.on_disconnect:
                        self.on_disconnect()
                logger.warning(f"Connection error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max)
        if self.ws:
            await self.ws.close()

    async def _manage_connection(
        self, ws: websockets.WebSocketClientProtocol
    ) -> None:
        receiver_task = asyncio.create_task(self._receive_loop(ws))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
        done, pending = await asyncio.wait(
            [receiver_task, heartbeat_task], return_when=asyncio.FIRST_EXCEPTION
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        async for message in ws:
            try:
                data = json.loads(message)
                self._handle_message(data)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid message format: {e}")

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                pong = await ws.ping()
                await asyncio.wait_for(pong, timeout=self.heartbeat_timeout)
            except Exception:
                logger.warning("Heartbeat failed")
                raise

    def _handle_message(self, message: Dict[str, Any]) -> None:
        message_type = message.get("type", "unknown")
        if message_type in self.message_handlers:
            for handler in self.message_handlers[message_type]:
                try:
                    handler(message)
                except Exception as e:
                    logger.error(f"Error in message handler: {e}")
        logger.debug(f"Received message: {message_type}")


__all__ = ["MessageBusClient"]


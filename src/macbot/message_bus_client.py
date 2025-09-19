#!/usr/bin/env python3
"""
MacBot Message Bus Client

Provides a robust WebSocket-based client with auto-reconnect for cross-process
communication. Falls back to the in-process queue bus when a WebSocket server
is not reachable but an in-memory bus is available.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# Try sync API first; fallback to async API if unavailable
_HAS_WEBSOCKETS_SYNC = False
_HAS_WEBSOCKETS_ASYNC = False
try:  # websockets >= 11 provides sync API
    from websockets.sync.client import connect as ws_connect  # type: ignore
    _HAS_WEBSOCKETS_SYNC = True
except Exception:
    try:
        import websockets  # type: ignore
        import asyncio
        _HAS_WEBSOCKETS_ASYNC = True
    except Exception:
        pass

from . import message_bus as message_bus_module  # in-process fallback
from .logging_utils import setup_logger


logger = setup_logger("macbot.message_bus_client", "logs/message_bus_client.log")


class MessageBusClient:
    """WebSocket message bus client with graceful fallback.

    - If a WS server is available at ws://host:port, connects and exchanges JSON messages.
    - Otherwise, uses the in-process queue bus if started in this process.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8082,
        service_type: str = "unknown",
        heartbeat_interval: float = 10.0,
        heartbeat_timeout: float = 5.0,
        reconnect_initial: float = 0.5,
        reconnect_max: float = 5.0,
        on_disconnect: Optional[Callable[[], None]] = None,
        on_reconnect: Optional[Callable[[], None]] = None,
    ) -> None:
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

        # Mode/state
        self._ws = None  # sync ws
        self._ws_async = None  # async ws
        self._loop = None  # asyncio loop for async ws
        self._use_ws = False
        self._inproc_queue = None  # type: ignore

        # Concurrency
        self._thread: Optional[threading.Thread] = None
        self._send_lock = threading.Lock()

        # Handlers
        self.message_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    # ---- Public API ----
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.client_id = f"{self.service_type}_{int(time.time())}"

        # Prefer WebSocket if available; otherwise fallback to in-proc bus
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"MessageBusClient starting for {self.service_type} ({self.client_id})")

    def stop(self) -> None:
        self.running = False
        # Close WS if any
        try:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None
            if self._ws_async is not None and self._loop is not None:
                try:
                    import asyncio
                    asyncio.run_coroutine_threadsafe(self._ws_async.close(), self._loop)
                except Exception:
                    pass
                self._ws_async = None
        finally:
            pass
        # Unregister from in-proc bus if used
        try:
            if self._inproc_queue is not None and self.client_id and message_bus_module.message_bus:
                message_bus_module.message_bus.unregister_client(self.client_id)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.connected = False
        logger.info(f"MessageBusClient stopped for {self.service_type}")

    def is_connected(self) -> bool:
        return bool(self.connected)

    def register_handler(self, message_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self.message_handlers.setdefault(message_type, []).append(handler)

    def unregister_handler(self, message_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        try:
            if message_type in self.message_handlers:
                self.message_handlers[message_type].remove(handler)
        except ValueError:
            pass

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None:
        self.on_disconnect = callback

    def set_reconnect_callback(self, callback: Callable[[], None]) -> None:
        self.on_reconnect = callback

    def send_message(self, message: Dict[str, Any]) -> None:
        """Send a message on the active transport."""
        if not self.connected:
            logger.warning("Not connected to message bus; dropping message")
            return

        enriched = {
            **message,
            "sender_id": self.client_id,
            "service_type": self.service_type,
            "timestamp": datetime.now().isoformat(),
        }

        if self._use_ws and self._ws is not None:
            try:
                payload = json.dumps(enriched)
                with self._send_lock:
                    self._ws.send(payload)
            except Exception as e:
                logger.warning(f"WebSocket send failed: {e}")
        elif self._use_ws and self._ws_async is not None and self._loop is not None:
            try:
                import asyncio
                payload = json.dumps(enriched)
                with self._send_lock:
                    asyncio.run_coroutine_threadsafe(self._ws_async.send(payload), self._loop)
            except Exception as e:
                logger.warning(f"Async WebSocket send failed: {e}")
        else:
            try:
                # in-proc broadcast
                if message_bus_module.message_bus is not None:
                    message_bus_module.message_bus.send_message(enriched)
                else:
                    raise RuntimeError("message bus not available")
            except Exception as e:
                logger.warning(f"In-proc send failed: {e}")

    # ---- Internals ----
    def _run(self) -> None:
        backoff = self.reconnect_initial
        first_connect = True
        while self.running:
            # Try WS first if available
            if _HAS_WEBSOCKETS_SYNC:
                try:
                    url = f"ws://{self.host}:{self.port}"
                    logger.info(f"Connecting to message bus WS {url}")
                    self._ws = ws_connect(url, open_timeout=self.heartbeat_timeout)
                    self._use_ws = True
                    self.connected = True
                    if not first_connect and self.on_reconnect:
                        try:
                            self.on_reconnect()
                        except Exception:
                            pass
                    first_connect = False
                    backoff = self.reconnect_initial
                    # Receive loop
                    for raw in self._ws:
                        if not self.running:
                            break
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        self._dispatch(msg)
                    # If loop exits, connection closed
                    self.connected = False
                    try:
                        if self.on_disconnect:
                            self.on_disconnect()
                    except Exception:
                        pass
                    # Ensure close
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
                except Exception as e:
                    # WS connect failed; fall back to in-proc if available
                    logger.debug(f"WS connect failed or closed: {e}")
                    self.connected = False
                    self._use_ws = False

            # Async websockets fallback
            if not self._use_ws and _HAS_WEBSOCKETS_ASYNC:
                try:
                    import asyncio
                    import websockets  # type: ignore

                    def runner():
                        self._loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(self._loop)

                        async def connect_and_listen():
                            url = f"ws://{self.host}:{self.port}"
                            try:
                                async with websockets.connect(url, open_timeout=self.heartbeat_timeout) as ws:
                                    self._ws_async = ws
                                    self._use_ws = True
                                    self.connected = True
                                    nonlocal_first = {'first': True}
                                    if not first_connect and self.on_reconnect:
                                        try:
                                            self.on_reconnect()
                                        except Exception:
                                            pass
                                    nonlocal_first['first'] = False
                                    while self.running:
                                        try:
                                            raw = await ws.recv()
                                        except Exception:
                                            break
                                        try:
                                            msg = json.loads(raw)
                                        except Exception:
                                            continue
                                        self._dispatch(msg)
                            finally:
                                self.connected = False
                                try:
                                    if self.on_disconnect:
                                        self.on_disconnect()
                                except Exception:
                                    pass

                        try:
                            self._loop.run_until_complete(connect_and_listen())
                        finally:
                            try:
                                self._loop.stop()
                            except Exception:
                                pass

                    t = threading.Thread(target=runner, daemon=True)
                    t.start()
                    # Give it a moment to connect or fail
                    time.sleep(min(0.2, self.heartbeat_timeout))
                    if self.connected:
                        first_connect = False
                        backoff = self.reconnect_initial
                    else:
                        # failed fast; continue to fallback
                        pass
                except Exception as e:
                    logger.debug(f"Async WS connect failed: {e}")

            # If not using WS, try in-proc bus (same-process only)
            if not self._use_ws and self.running:
                if message_bus_module.message_bus is None:
                    # No in-proc bus; wait and retry WS
                    time.sleep(min(backoff, self.reconnect_max))
                    backoff = min(backoff * 2, self.reconnect_max)
                    continue
                # Register and poll from queue
                try:
                    self._inproc_queue = message_bus_module.message_bus.register_client(
                        self.client_id or f"{self.service_type}_{int(time.time())}",
                        self.service_type,
                    )
                    self.connected = True
                    if not first_connect and self.on_reconnect:
                        try:
                            self.on_reconnect()
                        except Exception:
                            pass
                    first_connect = False
                    backoff = self.reconnect_initial
                    while self.running and self._inproc_queue is not None:
                        try:
                            msg = self._inproc_queue.get(timeout=0.25)
                            self._dispatch(msg)
                        except Exception:
                            # timeout or queue empty
                            pass
                except Exception as e:
                    logger.debug(f"In-proc bus unavailable: {e}")
                    self.connected = False
                finally:
                    # On exit, unregister
                    try:
                        if (self._inproc_queue is not None and self.client_id and
                                message_bus_module.message_bus):
                            message_bus_module.message_bus.unregister_client(self.client_id)
                    except Exception:
                        pass
                    self._inproc_queue = None

            # Backoff before next connect attempt if still running and not connected via WS
            if self.running and not self._use_ws:
                time.sleep(min(backoff, self.reconnect_max))
                backoff = min(backoff * 2, self.reconnect_max)

    def _dispatch(self, message: Dict[str, Any]) -> None:
        mtype = message.get("type", "")
        if not mtype:
            return
        handlers = self.message_handlers.get(mtype, [])
        for h in handlers:
            try:
                h(message)
            except Exception as e:
                logger.warning(f"Handler error for {mtype}: {e}")


__all__ = ["MessageBusClient"]

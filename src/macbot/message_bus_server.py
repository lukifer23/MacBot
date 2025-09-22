#!/usr/bin/env python3
"""
WebSocket Message Bus Server

Simple broadcast server for MacBot services. Pairs with MessageBusClient to
enable cross-process messaging (e.g., conversation interrupts, state updates).

This runs in-process inside the orchestrator on a background thread.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Set

from .logging_utils import setup_logger

logger = setup_logger("macbot.message_bus_server", "logs/message_bus_server.log")


try:
    from websockets.sync.server import serve  # type: ignore
except Exception as e:  # pragma: no cover - optional dep
    serve = None  # type: ignore
    logger.warning(f"websockets not available: {e}")


class WSMessageBusServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8082) -> None:
        self.host = host
        self.port = port
        self._clients: Set[Any] = set()
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def _handler(self, websocket):
        # Register
        self._clients.add(websocket)
        logger.info(f"Client connected. peers={len(self._clients)}")
        try:
            for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                # Broadcast to all peers
                payload = json.dumps(msg)
                for ws in list(self._clients):
                    try:
                        ws.send(payload)
                    except Exception:
                        # Drop on send failure
                        try:
                            self._clients.remove(ws)
                        except Exception:
                            pass
        finally:
            # Unregister
            try:
                self._clients.remove(websocket)
            except Exception:
                pass
            logger.info(f"Client disconnected. peers={len(self._clients)}")

    def start(self) -> None:
        if self._running:
            return
        if serve is None:
            logger.warning("Cannot start WS message bus server (websockets missing)")
            return

        def _run():
            if serve is None:
                logger.error("Cannot start WS server: websockets.serve is not available")
                return
            try:
                with serve(self._handler, self.host, self.port) as server:
                    self._server = server
                    logger.info(f"WS message bus on ws://{self.host}:{self.port}")
                    server.serve_forever()
            except Exception as e:
                logger.warning(f"WS message bus terminated: {e}")

        self._running = True
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            if self._server is not None:
                try:
                    self._server.shutdown()
                except Exception:
                    pass
                self._server = None
        finally:
            pass
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        # Close peers
        for ws in list(self._clients):
            try:
                ws.close()
            except Exception:
                pass
        self._clients.clear()
        logger.info("WS message bus stopped")


def start_message_bus_server(host: str = "127.0.0.1", port: int = 8082) -> WSMessageBusServer:
    srv = WSMessageBusServer(host, port)
    srv.start()
    return srv


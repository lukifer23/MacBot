"""Authenticated, bounded Unix-socket adapter for the native application."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings
from .config import save as save_settings
from .provision import model_dir, voice_model, voices
from .runtime import Runtime
from .task_protocol import PROTOCOL_OPERATIONS, TASK_PROTOCOL_VERSION, require_task_protocol

MAX_FRAME = 12 * 1024 * 1024


def _read_exact(connection: socket.socket, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        block = connection.recv(size - len(output))
        if not block:
            raise ConnectionError("Native client disconnected")
        output.extend(block)
    return bytes(output)


def read_frame(connection: socket.socket) -> dict[str, Any]:
    size = struct.unpack(">I", _read_exact(connection, 4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("Native IPC frame size is invalid")
    value = json.loads(_read_exact(connection, size))
    if not isinstance(value, dict):
        raise ValueError("Native IPC message must be an object")
    return value


def write_frame(connection: socket.socket, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > MAX_FRAME:
        raise ValueError("Native IPC response exceeds the frame limit")
    connection.sendall(struct.pack(">I", len(payload)) + payload)


class NativeIPCServer:
    def __init__(self, settings: Settings, runtime: Runtime):
        self.settings = settings
        self.runtime = runtime
        self.root = settings.data_dir / "run"
        self.path = self.root / "control.sock"
        if len(os.fsencode(self.path)) >= 100:
            digest = hashlib.sha256(str(settings.data_dir).encode()).hexdigest()[:16]
            self.path = (
                Path(tempfile.gettempdir()) / f"macbot-{os.getuid()}-{digest}" / "control.sock"
            )
        self.audio_path = self.path.with_name("audio.sock")
        self.token_path = self.root / "native-token"
        self.socket: socket.socket | None = None
        self.audio_socket: socket.socket | None = None
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.audio_thread: threading.Thread | None = None
        self.audio_connection: socket.socket | None = None
        self.audio_write_lock = threading.Lock()
        self.clients: set[threading.Thread] = set()
        self.client_connections: set[socket.socket] = set()
        self.lock = threading.Lock()
        self.token: str | None = None

    def start(self) -> bool:
        if not self.token_path.is_file():
            return False
        if self.token_path.stat().st_mode & 0o077:
            raise PermissionError("Native token permissions must be owner-only")
        token = self.token_path.read_text().strip()
        if len(token) != 64:
            raise ValueError("Native token is invalid")
        self.token = token
        self.token_path.unlink()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self.path.unlink(missing_ok=True)
        self.audio_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        listener.listen(4)
        listener.settimeout(0.25)
        self.socket = listener
        audio_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        audio_listener.bind(str(self.audio_path))
        os.chmod(self.audio_path, 0o600)
        audio_listener.listen(1)
        audio_listener.settimeout(0.25)
        self.audio_socket = audio_listener
        self.thread = threading.Thread(target=self._accept, name="native-ipc", daemon=True)
        self.thread.start()
        self.audio_thread = threading.Thread(
            target=self._accept_audio, name="native-audio-ipc", daemon=True
        )
        self.audio_thread.start()
        return True

    def _accept(self) -> None:
        assert self.socket is not None
        while not self.stopping.is_set():
            try:
                connection, _ = self.socket.accept()
            except TimeoutError:
                continue
            thread = threading.Thread(
                target=self._client, args=(connection,), name="native-ipc-client", daemon=True
            )
            with self.lock:
                self.clients.add(thread)
                self.client_connections.add(connection)
            thread.start()

    def _client(self, connection: socket.socket) -> None:
        current = threading.current_thread()
        connection.settimeout(25)
        try:
            hello = read_frame(connection)
            if hello != {
                "op": "authenticate",
                "token": self.token,
                "protocol_version": TASK_PROTOCOL_VERSION,
            }:
                write_frame(
                    connection,
                    {
                        "ok": False,
                        "error": "authentication_failed",
                        "failure": {
                            "code": "authentication_failed",
                            "message": "Native authentication failed",
                            "retryable": False,
                            "failure_class": "denied",
                        },
                    },
                )
                return
            write_frame(
                connection,
                {
                    "ok": True,
                    "protocol": TASK_PROTOCOL_VERSION,
                    "epoch": self.runtime.events.epoch,
                },
            )
            while not self.stopping.is_set():
                request = read_frame(connection)
                try:
                    response = self._dispatch(request)
                    write_frame(connection, {"ok": True, **response})
                except (ValueError, PermissionError, RuntimeError) as exc:
                    failure_class = (
                        "denied"
                        if isinstance(exc, PermissionError)
                        else "invalid_request"
                        if isinstance(exc, ValueError)
                        else "permanent"
                    )
                    write_frame(
                        connection,
                        {
                            "ok": False,
                            "error": type(exc).__name__,
                            "message": str(exc),
                            "failure": {
                                "code": type(exc).__name__,
                                "message": str(exc),
                                "retryable": False,
                                "failure_class": failure_class,
                            },
                        },
                    )
        except (ConnectionError, OSError, ValueError, json.JSONDecodeError):
            pass
        finally:
            connection.close()
            with self.lock:
                self.clients.discard(current)
                self.client_connections.discard(connection)

    def _accept_audio(self) -> None:
        assert self.audio_socket is not None
        while not self.stopping.is_set():
            try:
                connection, _ = self.audio_socket.accept()
            except TimeoutError:
                continue
            thread = threading.Thread(
                target=self._audio_client,
                args=(connection,),
                name="native-audio-client",
                daemon=True,
            )
            thread.start()

    def _audio_client(self, connection: socket.socket) -> None:
        connection.settimeout(5)
        try:
            hello = read_frame(connection)
            if hello != {
                "op": "authenticate",
                "token": self.token,
                "protocol_version": TASK_PROTOCOL_VERSION,
            }:
                write_frame(connection, {"ok": False, "error": "authentication_failed"})
                return
            write_frame(
                connection,
                {"ok": True, "protocol": TASK_PROTOCOL_VERSION, "sample_rate": 16000},
            )
            connection.settimeout(1)
            with self.audio_write_lock:
                previous = self.audio_connection
                self.audio_connection = connection
                self.runtime.native_audio_sender = self._send_audio
                if previous and previous is not connection:
                    previous.close()
            while not self.stopping.is_set():
                try:
                    size = struct.unpack(">I", _read_exact(connection, 4))[0]
                except TimeoutError:
                    continue
                if not 1 <= size <= 256 * 1024:
                    raise ValueError("Native audio frame size is invalid")
                frame = _read_exact(connection, size)
                if frame[0] == 1:
                    event = json.loads(frame[1:])
                    if not isinstance(event, dict) or len(frame) > 16_384:
                        raise ValueError("Native audio event is invalid")
                    self.runtime.native_audio_event(event)
                    continue
                if frame[0] != 2 or (len(frame) - 1) % 4:
                    raise ValueError("Native capture frame is invalid")
                samples = np.frombuffer(frame, dtype="<f4", offset=1)
                self.runtime.feed_native_audio(samples)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            with self.audio_write_lock:
                if self.audio_connection is connection:
                    self.audio_connection = None
                    self.runtime.native_audio_sender = None
                    self.runtime.native_audio_connected = False
                    self.runtime.native_aec = False
                    for completed in self.runtime.native_playback_done.values():
                        completed.set()
                    self.runtime.listen_native(False, session_id="native")
            connection.close()

    def _send_audio(self, operation: str, **values: Any) -> None:
        with self.audio_write_lock:
            connection = self.audio_connection
            if not connection:
                raise RuntimeError("Native playback connection is unavailable")
            generation = int(values["generation"])
            if operation == "pcm":
                samples = np.asarray(values["samples"], dtype="<f4")
                payload = (
                    bytes([3])
                    + struct.pack(">QI", generation, int(values["rate"]))
                    + samples.tobytes()
                )
            elif operation == "cancel":
                payload = bytes([4]) + struct.pack(">Q", generation)
            elif operation == "end":
                payload = bytes([5]) + struct.pack(">Q", generation)
            else:
                raise ValueError("Unknown native audio operation")
            connection.sendall(struct.pack(">I", len(payload)) + payload)

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        if request.get("protocol_version") != TASK_PROTOCOL_VERSION:
            raise ValueError(f"Native operations require protocol_version {TASK_PROTOCOL_VERSION}")
        if not isinstance(op, str) or op not in PROTOCOL_OPERATIONS - {"authenticate"}:
            raise ValueError("Unsupported native operation")
        if op == "sync":
            messages: list[dict[str, Any]] = []
            if self.runtime.history_store:
                for stored_message in self.runtime.history_store.load_messages(
                    "native", limit=2000
                ):
                    role = stored_message.get("role")
                    content = stored_message.get("content")
                    if role not in {"user", "assistant"} or not isinstance(content, str):
                        continue
                    messages.append(
                        {
                            "id": str(stored_message.get("_id", "")),
                            "role": role,
                            "content": content,
                            "created_at": int(stored_message.get("_created_ns", 0)),
                            "turn_id": stored_message.get("_turn_id"),
                        }
                    )
            tasks = self.runtime.task_engine.list("native") if self.runtime.task_engine else []
            with self.runtime.lock:
                turn = self.runtime.current
                active_turn = (
                    {
                        "id": turn.id,
                        "phase": turn.phase,
                        "user_text": turn.text,
                    }
                    if turn and not turn.terminal
                    else None
                )
            return {
                "protocol_version": TASK_PROTOCOL_VERSION,
                "epoch": self.runtime.events.epoch,
                "cursor": self.runtime.events.seq,
                "messages": messages,
                "tasks": tasks,
                "active_turn": active_turn,
            }
        if op == "status":
            status = self.runtime.status()
            try:
                response = self.runtime.tools.client.get(
                    self.settings.services.orchestrator.url + "/status",
                    headers=self.runtime.auth.headers("orchestrator"),
                )
                response.raise_for_status()
                status["supervisor"] = response.json()
            except Exception as exc:
                status["supervisor"] = {
                    "ready": False,
                    "error": f"Supervisor status unavailable: {type(exc).__name__}",
                }
            return {"status": status}
        if op == "settings":
            available_voices = []
            for voice in voices():
                try:
                    model_dir(self.settings, voice_model(voice))
                    installed = True
                except (FileNotFoundError, ValueError):
                    installed = False
                available_voices.append({"id": voice, "installed": installed})
            return {
                "settings": {
                    "diagnostics_enabled": self.settings.services.diagnostics_enabled,
                    "retention_days": self.settings.privacy.retention_days,
                    "endpoint_ms": self.settings.audio.endpoint_ms,
                    "context_length": self.settings.models.context_length,
                    "tts_voice": self.settings.models.tts_voice,
                    "voices": available_voices,
                }
            }
        if op == "update_settings":
            values = request.get("values")
            if not isinstance(values, dict) or set(values) - {
                "diagnostics_enabled",
                "retention_days",
                "endpoint_ms",
                "context_length",
                "tts_voice",
            }:
                raise ValueError("Unsupported settings update")
            candidate = self.settings.model_copy(deep=True)
            if "diagnostics_enabled" in values:
                value = values["diagnostics_enabled"]
                if not isinstance(value, bool):
                    raise ValueError("Diagnostics setting must be boolean")
                candidate.services.diagnostics_enabled = value
            for key, target in {
                "retention_days": candidate.privacy,
                "endpoint_ms": candidate.audio,
                "context_length": candidate.models,
            }.items():
                if key in values:
                    value = values[key]
                    if type(value) is not int:
                        raise ValueError(f"{key} must be an integer")
                    setattr(target, key, value)
            if "tts_voice" in values:
                voice = values["tts_voice"]
                if not isinstance(voice, str) or voice not in voices():
                    raise ValueError("TTS voice is not registered")
                model_dir(self.settings, voice_model(voice))
                candidate.models.tts_voice = voice
            save_settings(candidate)
            self.settings.services = candidate.services
            self.settings.privacy = candidate.privacy
            self.settings.audio = candidate.audio
            self.settings.models = candidate.models
            return {"state": "saved", "restart_required": True}
        if op == "events":
            after = request.get("after", 0)
            epoch = request.get("epoch")
            if (
                type(after) is not int
                or after < 0
                or (epoch is not None and not isinstance(epoch, str))
            ):
                raise ValueError("Invalid event cursor")
            return self.runtime.events.read(after, timeout=20, epoch=epoch, session_id="native")
        if op == "chat":
            speak = request.get("speak", True)
            if not isinstance(speak, bool):
                raise ValueError("speak must be boolean")
            turn = self.runtime.submit(
                request.get("message"),
                speak=speak,
                session_id="native",
            )
            return {"state": "accepted", "turn_id": turn.id}
        if op == "task_create":
            require_task_protocol(request)
            if not self.runtime.task_engine:
                raise RuntimeError("Durable Task mode requires encrypted history")
            message = request.get("message")
            if not isinstance(message, str):
                raise ValueError("Task message must be text")
            task = self.runtime.task_engine.create(message, "native")
            return {
                "protocol_version": TASK_PROTOCOL_VERSION,
                "task": self.runtime.task_engine.snapshot(task, event="proposed"),
            }
        if op == "task_list":
            require_task_protocol(request)
            if not self.runtime.task_engine:
                return {"protocol_version": TASK_PROTOCOL_VERSION, "tasks": []}
            return {
                "protocol_version": TASK_PROTOCOL_VERSION,
                "tasks": self.runtime.task_engine.list("native"),
            }
        if op == "task_command":
            require_task_protocol(request)
            if not self.runtime.task_engine:
                raise RuntimeError("Durable Task mode requires encrypted history")
            task_id = request.get("task_id")
            command = request.get("command")
            if not isinstance(task_id, str) or not isinstance(command, str):
                raise ValueError("Task command requires task_id and command")
            if command == "authorize":
                task = self.runtime.task_engine.authorize(task_id, "native", True)
            elif command == "deny":
                task = self.runtime.task_engine.authorize(task_id, "native", False)
            elif command == "pause":
                task = self.runtime.task_engine.pause(task_id, "native")
            elif command == "resume":
                task = self.runtime.task_engine.resume(task_id, "native")
            elif command == "cancel":
                task = self.runtime.task_engine.cancel(task_id, "native")
            else:
                raise ValueError("Unsupported Task command")
            return {
                "protocol_version": TASK_PROTOCOL_VERSION,
                "task": self.runtime.task_engine.snapshot(task),
            }
        if op == "preview_voice":
            turn = self.runtime.submit(
                "Hey, I’m MacBot. I’m ready when you are.",
                speak=True,
                synthesis_only=True,
                session_id="native",
            )
            return {"state": "accepted", "turn_id": turn.id}
        if op == "listen":
            enabled = request.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be boolean")
            self.runtime.listen_native(enabled, session_id="native")
            return {"audio": self.runtime.audio_status()}
        if op == "interrupt":
            self.runtime.interrupt()
            return {"state": "interrupted"}
        if op == "clear":
            self.runtime.clear(session_id="native")
            return {"state": "cleared"}
        if op == "documents":
            response = self.runtime.tools.client.get(
                self.settings.services.rag.url + "/api/documents",
                headers=self.runtime.auth.headers("rag"),
            )
            response.raise_for_status()
            return response.json()
        if op == "document_import":
            import base64

            from .document_parser import extract

            name = request.get("name")
            suffix = request.get("suffix")
            encoded = request.get("content")
            if not isinstance(name, str) or not 0 < len(name) <= 255:
                raise ValueError("Invalid document name")
            if suffix not in {".txt", ".pdf", ".docx"} or not isinstance(encoded, str):
                raise ValueError("Invalid document payload")
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError("Invalid document encoding") from exc
            text = extract(content, suffix)
            response = self.runtime.tools.client.post(
                self.settings.services.rag.url + "/api/documents",
                headers=self.runtime.auth.headers("rag"),
                json={"content": text, "title": name, "type": suffix.removeprefix(".")},
            )
            response.raise_for_status()
            return response.json()
        if op == "document_delete":
            doc_id = request.get("id")
            if not isinstance(doc_id, str) or not doc_id:
                raise ValueError("Invalid document ID")
            response = self.runtime.tools.client.delete(
                self.settings.services.rag.url + "/api/documents/" + doc_id,
                headers=self.runtime.auth.headers("rag"),
            )
            if response.status_code not in {200, 404}:
                response.raise_for_status()
            return {"deleted": response.status_code == 200}
        if op == "document_search":
            query = request.get("query")
            if not isinstance(query, str):
                raise ValueError("Invalid document query")
            response = self.runtime.tools.client.post(
                self.settings.services.rag.url + "/api/search",
                headers=self.runtime.auth.headers("rag"),
                json={"query": query, "top_k": 5},
            )
            response.raise_for_status()
            return response.json()
        raise ValueError("Unknown native operation")

    def close(self) -> None:
        self.stopping.set()
        if self.socket:
            self.socket.close()
        if self.audio_socket:
            self.audio_socket.close()
        with self.lock:
            control_connections = list(self.client_connections)
        for connection in control_connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        with self.audio_write_lock:
            if self.audio_connection:
                self.audio_connection.close()
            self.audio_connection = None
            self.runtime.native_audio_sender = None
            self.runtime.native_audio_connected = False
            self.runtime.native_aec = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.audio_thread:
            self.audio_thread.join(timeout=2)
        with self.lock:
            client_threads = list(self.clients)
        for thread in client_threads:
            thread.join(timeout=2)
        self.path.unlink(missing_ok=True)
        self.audio_path.unlink(missing_ok=True)

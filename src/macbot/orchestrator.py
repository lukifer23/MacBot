"""Supervise only processes started by this instance. No global process matching."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, TextIO
from urllib.parse import urlsplit

import httpx
import psutil
from flask import Flask, jsonify, request

from .auth import AuthStore, install_security
from .config import Settings, atomic_write, load, prepare
from .history import runtime_history_key
from .provision import model_file


@dataclass
class ServiceDefinition:
    name: str
    command: list[str]
    health_endpoint: str | None = None
    port: int | None = None
    env: dict = field(default_factory=dict)
    cwd: str | None = None
    dependencies: tuple[str, ...] = ()


class MacBotOrchestrator:
    def __init__(self, settings: Settings | None = None, history_key: bytes | None = None):
        self.settings = settings or load()
        prepare(self.settings)
        if history_key is not None and len(history_key) != 32:
            raise ValueError("History encryption requires a 256-bit key")
        self.history_key = history_key
        self.auth = AuthStore(self.settings.data_dir)
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, BinaryIO] = {}
        self.service_definitions: dict[str, ServiceDefinition] = {}
        self.restarts: dict[str, int] = {}
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.client = httpx.Client(timeout=2, trust_env=False)
        self.failures: dict[str, str] = {}
        self.readiness: dict[str, bool] = {}
        self.health_failures: dict[str, int] = {}
        self.healthy_since: dict[str, float] = {}
        self._instance_file: TextIO | None = None

    def definitions(self):
        s = self.settings
        package_root = str(Path(__file__).resolve().parents[1])
        python_path = os.environ.get("PYTHONPATH")
        inherited = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "LC_CTYPE",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "REQUESTS_CA_BUNDLE",
            }
        }
        common = {
            **inherited,
            "MACBOT_DATA_DIR": str(s.data_dir),
            "MACBOT_CONFIG": str(s.config_path),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "ANONYMIZED_TELEMETRY": "False",
            "DO_NOT_TRACK": "1",
            "PYTHONPATH": (
                package_root + os.pathsep + python_path if python_path else package_root
            ),
        }
        # A descriptor received by the supervisor has already been consumed.
        # Each assistant launch receives a fresh pipe containing the in-memory key.
        common.pop("MACBOT_HISTORY_KEY_FD", None)
        if s.models.llm_backend == "llama":
            keyfile = s.data_dir / "run" / "llama-key"
            atomic_write(keyfile, self.auth.keys["llm"].encode())
            url = urlsplit(s.models.llm_url)
            assert url.hostname is not None
            command = [
                str(s.data_dir / "bin/llama-server"),
                "-m",
                str(model_file(s, s.models.llm, ".gguf")),
                "--host",
                url.hostname,
                "--port",
                str(url.port),
                "-c",
                str(s.models.context_length),
                "-t",
                str(s.models.threads),
                "-ngl",
                "999",
                "-np",
                "1",
                "--jinja",
                "--reasoning",
                "off",
                "--api-key-file",
                str(keyfile),
            ]
            self.service_definitions["llm"] = ServiceDefinition(
                "llm", command, s.models.llm_url + "/v1/models", url.port, common
            )
        modules = [
            ("rag", "rag_server", ()),
            ("assistant", "voice_assistant", ("llm", "rag")),
        ]
        if s.services.diagnostics_enabled:
            modules.append(("dashboard", "web_dashboard", ("assistant",)))
        for name, module, dependencies in modules:
            endpoint = s.endpoint(name)
            self.service_definitions[name] = ServiceDefinition(
                name,
                [sys.executable, "-m", "macbot." + module],
                endpoint.url + "/ready",
                endpoint.port,
                common,
                dependencies=dependencies,
            )

    def acquire(self):
        self._instance_file = (self.settings.data_dir / "run/orchestrator.lock").open("a+")
        try:
            fcntl.flock(self._instance_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another MacBot supervisor owns this data directory") from None
        atomic_write(
            self.settings.data_dir / "run/orchestrator.json",
            json.dumps({"pid": os.getpid(), "created": psutil.Process().create_time()}).encode(),
        )

    def start_service(
        self, service: ServiceDefinition, retries: int = 60, backoff: float = 0.5
    ) -> dict:
        with self.lifecycle_lock:
            return self._start_service(service, retries, backoff)

    def _start_service(self, service: ServiceDefinition, retries: int, backoff: float) -> dict:
        with self.lock:
            self.readiness[service.name] = False
            if service.name in self.processes and self.processes[service.name].poll() is None:
                return {"success": False, "error": "Service is already owned and running"}
            if service.port:
                host = urlsplit(service.health_endpoint or "").hostname or "127.0.0.1"
                family = socket.AF_INET6 if ":" in host else socket.AF_INET
                with socket.socket(family) as sock:
                    # Match the actual server's bind semantics: TIME_WAIT from
                    # a closed stream is not an unrelated live listener.
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.bind((host, service.port))
                    except OSError:
                        self.failures[service.name] = (
                            f"Port {service.port} is occupied; no process was stopped"
                        )
                        return {
                            "success": False,
                            "error": self.failures[service.name],
                        }
            log = (self.settings.data_dir / "logs" / (service.name + ".log")).open("ab")
            read_fd: int | None = None
            try:
                environment = dict(service.env) if service.env else None
                pass_fds: tuple[int, ...] = ()
                if service.name == "assistant" and self.history_key is not None:
                    read_fd, write_fd = os.pipe()
                    try:
                        os.write(write_fd, self.history_key)
                    finally:
                        os.close(write_fd)
                    environment = environment or dict(os.environ)
                    environment["MACBOT_HISTORY_KEY_FD"] = str(read_fd)
                    pass_fds = (read_fd,)
                process = subprocess.Popen(
                    service.command,
                    stdout=log,
                    stderr=log,
                    env=environment,
                    cwd=service.cwd,
                    pass_fds=pass_fds,
                    start_new_session=True,
                )
            except Exception:
                log.close()
                raise
            finally:
                if read_fd is not None:
                    os.close(read_fd)
            self.processes[service.name], self.logs[service.name] = process, log
        for _ in range(retries):
            if process.poll() is not None:
                break
            if not service.health_endpoint:
                return {"success": True, "pid": process.pid}
            try:
                r = self.client.get(
                    service.health_endpoint, headers=self.auth.headers(service.name)
                )
                if r.is_success and (service.name == "llm" or r.json().get("pid") == process.pid):
                    self.failures.pop(service.name, None)
                    self.readiness[service.name] = True
                    self.health_failures[service.name] = 0
                    return {"success": True, "pid": process.pid}
            except (httpx.HTTPError, ValueError):
                pass
            if self.stopping.wait(backoff):
                break
        self.stop_service(service.name)
        self.failures[service.name] = "Service failed readiness; inspect its private log"
        return {"success": False, "error": self.failures[service.name]}

    def stop_service(self, name: str):
        with self.lifecycle_lock:
            self._stop_service(name)

    def _stop_service(self, name: str):
        with self.lock:
            self.readiness[name] = False
            process = self.processes.pop(name, None)
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
            log = self.logs.pop(name, None)
            if log:
                log.close()

    def restart_process(self, name: str) -> dict:
        with self.lifecycle_lock:
            if name not in self.service_definitions:
                raise ValueError("Unknown managed service")
            self.stop_service(name)
            return self.start_service(self.service_definitions[name])

    def start_all(self) -> bool:
        self.definitions()
        for service in self.service_definitions.values():
            result = self.start_service(service)
            if not result["success"]:
                self.stop_all()
                raise RuntimeError(f"{service.name}: {result['error']}")
        return True

    def status(self) -> dict:
        with self.lock:
            services = {}
            for name, definition in self.service_definitions.items():
                process = self.processes.get(name)
                alive = bool(process and process.poll() is None)
                rss = None
                if alive and process is not None:
                    try:
                        parent = psutil.Process(process.pid)
                        rss = sum(
                            p.memory_info().rss for p in [parent, *parent.children(recursive=True)]
                        )
                    except psutil.Error:
                        pass
                services[name] = {
                    "running": alive,
                    "pid": process.pid if process else None,
                    "rss_bytes": rss,
                    "ready": alive and self.readiness.get(name, False),
                    "port": definition.port,
                    "error": self.failures.get(name),
                    "restarts": self.restarts.get(name, 0),
                    "dependencies": list(definition.dependencies),
                    "state": (
                        "ready"
                        if alive and self.readiness.get(name, False)
                        else "recovering"
                        if alive
                        else "failed"
                    ),
                }
            return {
                "pid": os.getpid(),
                "supervisor_rss_bytes": psutil.Process().memory_info().rss,
                "services": services,
                "ready": bool(services)
                and all(s["ready"] and not s["error"] for s in services.values()),
            }

    def monitor(self):
        while not self.stopping.wait(2):
            for name in list(self.service_definitions):
                if not self.lifecycle_lock.acquire(blocking=False):
                    continue
                try:
                    self._check_service(name)
                finally:
                    self.lifecycle_lock.release()

    def _check_service(self, name: str):
        definition = self.service_definitions[name]
        unavailable = [
            dependency
            for dependency in definition.dependencies
            if not self.readiness.get(dependency)
        ]
        if unavailable:
            self.readiness[name] = False
            self.failures[name] = "Blocked by unavailable dependencies: " + ", ".join(unavailable)
            self.healthy_since.pop(name, None)
            return
        process = self.processes.get(name)
        healthy = bool(process and process.poll() is None)
        if healthy and process is not None and definition.health_endpoint:
            try:
                response = self.client.get(
                    definition.health_endpoint, headers=self.auth.headers(name)
                )
                healthy = response.is_success and (
                    name == "llm" or response.json().get("pid") == process.pid
                )
            except (httpx.HTTPError, ValueError):
                healthy = False
        self.readiness[name] = healthy
        if healthy:
            self.health_failures[name] = 0
            self.failures.pop(name, None)
            since = self.healthy_since.setdefault(name, time.monotonic())
            if time.monotonic() - since >= 60:
                self.restarts[name] = 0
            return
        self.healthy_since.pop(name, None)
        self.health_failures[name] = self.health_failures.get(name, 0) + 1
        if process and process.poll() is None and self.health_failures[name] < 3:
            return
        if not healthy:
            count = self.restarts.get(name, 0)
            if count >= 3:
                self.failures[name] = "Restart limit reached"
                return
            self.restarts[name] = count + 1
            if self.stopping.wait(min(2**count, 8)):
                return
            self.restart_process(name)

    def stop_all(self):
        self.stopping.set()
        for name in reversed(list(self.processes)):
            self.stop_service(name)


def create_app(supervisor: MacBotOrchestrator):
    app = Flask(__name__)
    install_security(app, supervisor.settings, "orchestrator", supervisor.auth)

    @app.get("/health")
    def health():
        return jsonify(status="alive")

    @app.get("/ready")
    @app.get("/status")
    @app.get("/services")
    @app.get("/metrics")
    def status():
        data = supervisor.status()
        # Status must remain readable during recovery; only readiness is a gate.
        return jsonify(data), 503 if request.path == "/ready" and not data["ready"] else 200

    @app.post("/service/<name>/restart")
    def restart(name):
        if name not in supervisor.service_definitions:
            return jsonify(error="Unknown service"), 404
        result = supervisor.restart_process(name)
        return jsonify(result), 200 if result["success"] else 503

    @app.post("/shutdown")
    def shutdown():
        supervisor.stopping.set()
        return jsonify(state="accepted"), 202

    return app


def main():
    from werkzeug.serving import make_server

    settings = load()
    history_key = runtime_history_key()
    if settings.privacy.history_enabled and history_key is None:
        raise RuntimeError("Encrypted history requires an inherited private key pipe")
    supervisor = MacBotOrchestrator(settings, history_key=history_key)
    supervisor.acquire()
    server = make_server(
        supervisor.settings.services.orchestrator.host,
        supervisor.settings.services.orchestrator.port,
        create_app(supervisor),
        threaded=True,
    )
    signal.signal(signal.SIGTERM, lambda *_: supervisor.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: supervisor.stopping.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        supervisor.start_all()
        supervisor.monitor()
    finally:
        supervisor.stop_all()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        supervisor.client.close()
        supervisor.auth.close()
        if supervisor._instance_file:
            supervisor._instance_file.close()


if __name__ == "__main__":
    main()

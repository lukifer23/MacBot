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
from typing import Any, BinaryIO, TextIO
from urllib.parse import urlsplit

import httpx
import psutil
from flask import Flask, jsonify, request

from .auth import AuthStore, install_security
from .config import Settings, atomic_write, load, prepare
from .history import runtime_history_key
from .provision import model_file
from .residency import InferenceResidencyLease


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
    def __init__(
        self,
        settings: Settings | None = None,
        history_key: bytes | None = None,
        *,
        residency_dir: Path | None = None,
    ):
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
        self.lifecycle: dict[str, dict[str, Any]] = {}
        self._instance_file: TextIO | None = None
        self._residency_lease = InferenceResidencyLease(
            self.settings.data_dir, lease_dir=residency_dir
        )

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
        for name in self.service_definitions:
            self._transition(name, "pending")

    def _transition(self, name: str, phase: str, **details: Any) -> None:
        """Record observable lifecycle progress without making status block on a child."""
        now_ns = time.time_ns()
        monotonic_ns = time.monotonic_ns()
        with self.lock:
            lifecycle = self.lifecycle.setdefault(
                name,
                {
                    "phase": "pending",
                    "phase_changed_at_ns": now_ns,
                    "phase_changed_monotonic_ns": monotonic_ns,
                    "spawned_at_ns": None,
                    "spawned_monotonic_ns": None,
                    "ready_at_ns": None,
                    "ready_monotonic_ns": None,
                    "stop_requested_at_ns": None,
                    "stop_requested_monotonic_ns": None,
                    "exited_at_ns": None,
                    "exited_monotonic_ns": None,
                    "readiness_attempts": 0,
                    "last_probe_ms": None,
                    "forced_kill": False,
                    "exit_code": None,
                },
            )
            if lifecycle["phase"] != phase:
                lifecycle["phase"] = phase
                lifecycle["phase_changed_at_ns"] = now_ns
                lifecycle["phase_changed_monotonic_ns"] = monotonic_ns
            lifecycle.update(details)

    @staticmethod
    def _startup_phase(name: str) -> str:
        return {
            "llm": "loading_language_model",
            "rag": "loading_retrieval",
            "assistant": "loading_speech_models",
            "dashboard": "starting_diagnostics",
        }.get(name, "waiting_readiness")

    def acquire(self):
        self._residency_lease.acquire()
        try:
            self._instance_file = (self.settings.data_dir / "run/orchestrator.lock").open("a+")
            try:
                fcntl.flock(self._instance_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another MacBot supervisor owns this data directory") from None
            atomic_write(
                self.settings.data_dir / "run/orchestrator.json",
                json.dumps(
                    {"pid": os.getpid(), "created": psutil.Process().create_time()}
                ).encode(),
            )
        except Exception:
            if self._instance_file:
                self._instance_file.close()
            self._instance_file = None
            self._residency_lease.release()
            raise

    def release(self) -> None:
        """Release data-directory and host ownership after all children stop."""
        if self._instance_file:
            self._instance_file.close()
            self._instance_file = None
        self._residency_lease.release()

    def start_service(
        self, service: ServiceDefinition, retries: int = 60, backoff: float = 0.5
    ) -> dict:
        with self.lifecycle_lock:
            return self._start_service(service, retries, backoff)

    def _start_service(self, service: ServiceDefinition, retries: int, backoff: float) -> dict:
        if retries < 1 or backoff <= 0:
            raise ValueError("Service readiness requires positive retries and backoff")
        startup_started = time.monotonic()
        deadline = startup_started + retries * backoff
        self._transition(
            service.name,
            "spawning",
            spawned_at_ns=None,
            spawned_monotonic_ns=None,
            ready_at_ns=None,
            ready_monotonic_ns=None,
            stop_requested_at_ns=None,
            stop_requested_monotonic_ns=None,
            exited_at_ns=None,
            exited_monotonic_ns=None,
            readiness_attempts=0,
            last_probe_ms=None,
            forced_kill=False,
            exit_code=None,
        )
        with self.lock:
            if service.name in self.processes and self.processes[service.name].poll() is None:
                return {"success": False, "error": "Service is already owned and running"}
            self.readiness[service.name] = False
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
                        self._transition(service.name, "failed")
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
            spawned_at_ns = time.time_ns()
            spawned_monotonic_ns = time.monotonic_ns()
            self._transition(
                service.name,
                self._startup_phase(service.name),
                spawned_at_ns=spawned_at_ns,
                spawned_monotonic_ns=spawned_monotonic_ns,
            )
        attempts = 0
        while attempts < retries and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if not service.health_endpoint:
                ready_at_ns = time.time_ns()
                ready_monotonic_ns = time.monotonic_ns()
                self._transition(
                    service.name,
                    "ready",
                    ready_at_ns=ready_at_ns,
                    ready_monotonic_ns=ready_monotonic_ns,
                )
                return {
                    "success": True,
                    "pid": process.pid,
                    "startup_ms": (time.monotonic() - startup_started) * 1000,
                }
            attempts += 1
            probe_started = time.monotonic()
            try:
                remaining = max(0.001, deadline - probe_started)
                r = self.client.get(
                    service.health_endpoint,
                    headers=self.auth.headers(service.name),
                    timeout=min(2.0, remaining),
                )
                probe_ms = (time.monotonic() - probe_started) * 1000
                self._transition(
                    service.name,
                    self._startup_phase(service.name),
                    readiness_attempts=attempts,
                    last_probe_ms=probe_ms,
                )
                if r.is_success and (service.name == "llm" or r.json().get("pid") == process.pid):
                    self.failures.pop(service.name, None)
                    self.readiness[service.name] = True
                    self.health_failures[service.name] = 0
                    ready_at_ns = time.time_ns()
                    ready_monotonic_ns = time.monotonic_ns()
                    self._transition(
                        service.name,
                        "ready",
                        ready_at_ns=ready_at_ns,
                        ready_monotonic_ns=ready_monotonic_ns,
                    )
                    return {
                        "success": True,
                        "pid": process.pid,
                        "startup_ms": (time.monotonic() - startup_started) * 1000,
                        "readiness_attempts": attempts,
                    }
            except (httpx.HTTPError, ValueError):
                self._transition(
                    service.name,
                    self._startup_phase(service.name),
                    readiness_attempts=attempts,
                    last_probe_ms=(time.monotonic() - probe_started) * 1000,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.stopping.wait(min(backoff, remaining)):
                break
        self.stop_service(service.name)
        elapsed_ms = (time.monotonic() - startup_started) * 1000
        self.failures[service.name] = (
            f"Service failed readiness after {attempts} probes in {elapsed_ms:.0f} ms; "
            "inspect its private log"
        )
        self._transition(service.name, "failed")
        return {"success": False, "error": self.failures[service.name]}

    def stop_service(self, name: str):
        with self.lifecycle_lock:
            self._stop_service(name)

    def _stop_service(self, name: str):
        with self.lock:
            self.readiness[name] = False
            process = self.processes.get(name)
            log = self.logs.get(name)
            self._transition(
                name,
                "stopping",
                stop_requested_at_ns=time.time_ns(),
                stop_requested_monotonic_ns=time.monotonic_ns(),
            )
        forced_kill = False
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forced_kill = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        with self.lock:
            if self.processes.get(name) is process:
                self.processes.pop(name, None)
            if self.logs.get(name) is log:
                self.logs.pop(name, None)
            if log:
                log.close()
            self._transition(
                name,
                "stopped",
                exited_at_ns=time.time_ns(),
                exited_monotonic_ns=time.monotonic_ns(),
                forced_kill=forced_kill,
                exit_code=process.returncode if process else None,
            )

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
            now_monotonic_ns = time.monotonic_ns()
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
                lifecycle = self.lifecycle.get(name, {})
                spawned = lifecycle.get("spawned_monotonic_ns")
                ready = lifecycle.get("ready_monotonic_ns")
                stop_requested = lifecycle.get("stop_requested_monotonic_ns")
                exited = lifecycle.get("exited_monotonic_ns")
                services[name] = {
                    "running": alive,
                    "pid": process.pid if process else None,
                    "rss_bytes": rss,
                    "ready": alive and self.readiness.get(name, False),
                    "port": definition.port,
                    "error": self.failures.get(name),
                    "restarts": self.restarts.get(name, 0),
                    "dependencies": list(definition.dependencies),
                    "state": lifecycle.get("phase", "pending"),
                    "phase": lifecycle.get("phase", "pending"),
                    "phase_changed_at_ns": lifecycle.get("phase_changed_at_ns"),
                    "phase_elapsed_ms": (
                        (now_monotonic_ns - lifecycle["phase_changed_monotonic_ns"]) / 1e6
                        if lifecycle.get("phase_changed_monotonic_ns")
                        else None
                    ),
                    "startup_ms": (
                        ((ready or exited or now_monotonic_ns) - spawned) / 1e6 if spawned else None
                    ),
                    "shutdown_ms": (
                        ((exited or now_monotonic_ns) - stop_requested) / 1e6
                        if stop_requested
                        else None
                    ),
                    "readiness_attempts": lifecycle.get("readiness_attempts", 0),
                    "last_probe_ms": lifecycle.get("last_probe_ms"),
                    "forced_kill": lifecycle.get("forced_kill", False),
                    "exit_code": lifecycle.get("exit_code"),
                }
            ready = (
                not self.stopping.is_set()
                and bool(services)
                and all(s["ready"] and not s["error"] for s in services.values())
            )
            active_phase = "ready" if ready else "pending"
            if self.stopping.is_set():
                active_phase = "stopping"
            elif services:
                active_phase = next(
                    (
                        str(service["phase"])
                        for service in services.values()
                        if service["phase"] != "ready"
                    ),
                    "ready",
                )
            return {
                "pid": os.getpid(),
                "supervisor_rss_bytes": psutil.Process().memory_info().rss,
                "inference_residency": self._residency_lease.owner,
                "services": services,
                "phase": active_phase,
                "shutdown_requested": self.stopping.is_set(),
                "ready": ready,
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
            self._transition(name, "blocked_dependencies")
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
            self._transition(name, "ready")
            return
        self.healthy_since.pop(name, None)
        self.health_failures[name] = self.health_failures.get(name, 0) + 1
        if process and process.poll() is None and self.health_failures[name] < 3:
            self._transition(name, "recovering")
            return
        if not healthy:
            count = self.restarts.get(name, 0)
            if count >= 3:
                self.failures[name] = "Restart limit reached"
                self._transition(name, "failed")
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
        supervisor.release()


if __name__ == "__main__":
    main()

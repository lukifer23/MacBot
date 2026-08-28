"""A single tool registry. Side effects execute only through bound approvals."""

from __future__ import annotations

import json
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import psutil

from .auth import AuthStore
from .config import Settings

SCHEMAS: dict[str, tuple[str, dict[str, str]]] = {
    "system_info": ("Read CPU, memory and disk usage on this Mac", {}),
    "rag_search": ("Search documents in the local knowledge base", {"query": "string"}),
    "open_app": ("Open an allowed application, after user confirmation", {"app": "string"}),
    "web_search": (
        "Search the external web only when the user asks for an internet search or current information. Never use for ordinary factual questions, local documents, or weather (use weather). Requires user confirmation.",
        {"query": "string"},
    ),
    "browse_website": (
        "Open a public HTTP(S) website in Safari, after user confirmation",
        {"url": "string"},
    ),
    "screenshot": ("Save a screenshot locally, after user confirmation", {}),
    "weather": ("Open a web weather search, after user confirmation", {"location": "string"}),
}
READ_ONLY = {"system_info", "rag_search"}


@dataclass(frozen=True)
class PendingAction:
    id: str
    session_id: str
    turn_id: str
    name: str
    arguments_json: str
    expires: float


class Tools:
    def __init__(self, settings: Settings, auth: AuthStore):
        self.settings, self.auth = settings, auth
        self.pending: dict[str, PendingAction] = {}
        self.lock = threading.RLock()
        self.client = httpx.Client(timeout=8, trust_env=False)

    def definitions(self) -> list[dict]:
        definitions: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": SCHEMAS[name][0],
                    "parameters": {
                        "type": "object",
                        "properties": {k: {"type": t} for k, t in SCHEMAS[name][1].items()},
                        "required": list(SCHEMAS[name][1]),
                        "additionalProperties": False,
                    },
                },
            }
            for name in self.settings.tools.enabled
            if name in SCHEMAS
        ]
        for definition in definitions:
            function = definition["function"]
            if function["name"] == "open_app":
                function["parameters"]["properties"]["app"]["enum"] = list(
                    self.settings.tools.allowed_apps
                )
        return definitions

    def validate(self, name: str, arguments: Any) -> dict:
        if name not in self.settings.tools.enabled or name not in SCHEMAS:
            raise PermissionError("Tool is disabled or unknown")
        if not isinstance(arguments, dict) or set(arguments) != set(SCHEMAS[name][1]):
            raise ValueError("Tool arguments do not match its schema")
        if any(not isinstance(v, str) or len(v) > 2000 or "\x00" in v for v in arguments.values()):
            raise ValueError("Invalid tool argument value")
        if name == "open_app" and arguments["app"] not in self.settings.tools.allowed_apps:
            raise PermissionError("Application is not allowed")
        if name == "browse_website":
            u = urlsplit(arguments["url"])
            if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
                raise ValueError("A complete HTTP(S) URL without credentials is required")
        return dict(arguments)

    def request(self, session_id: str, turn_id: str, name: str, arguments: dict) -> PendingAction:
        args = self.validate(name, arguments)
        if name in READ_ONLY:
            raise ValueError("Read-only tools do not require approvals")
        action = PendingAction(
            secrets.token_urlsafe(24),
            session_id,
            turn_id,
            name,
            json.dumps(args, sort_keys=True),
            time.monotonic() + self.settings.tools.approval_seconds,
        )
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v.expires > time.monotonic()}
            if len(self.pending) >= 8:
                raise RuntimeError("Too many pending actions")
            self.pending[action.id] = action
        return action

    def invalidate(self, turn_id: str) -> None:
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v.turn_id != turn_id}

    def decide(self, action_id: str, session_id: str, turn_id: str, approve: bool) -> dict:
        action = self.consume(action_id, session_id, turn_id)
        if not approve:
            return {"status": "denied", "tool": action.name}
        return self._execute(action.name, json.loads(action.arguments_json))

    def consume(self, action_id: str, session_id: str, turn_id: str) -> PendingAction:
        """Claim once under the policy lock; execute outside latency-sensitive locks."""
        with self.lock:
            action = self.pending.get(action_id)
            if not action or action.session_id != session_id or action.turn_id != turn_id:
                raise PermissionError("Approval does not belong to this session and turn")
            del self.pending[action_id]
            if action.expires <= time.monotonic():
                raise PermissionError("Approval expired")
            return action

    def read(self, name: str, arguments: dict) -> dict:
        if name not in READ_ONLY:
            raise PermissionError("Explicit approval required")
        return self._execute(name, arguments)

    def _execute(self, name: str, arguments: dict) -> dict:
        args = self.validate(name, arguments)
        if name == "system_info":
            return {
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage("/").percent,
            }
        if name == "rag_search":
            r = self.client.post(
                self.settings.services.rag.url + "/api/search",
                json={"query": args["query"], "top_k": 5},
                headers=self.auth.headers("rag"),
            )
            r.raise_for_status()
            return r.json()
        if name == "screenshot":
            directory = Path(self.settings.tools.screenshot_dir).expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / ("macbot-" + secrets.token_hex(8) + ".png")
            subprocess.run(["screencapture", "-x", str(path)], check=True, timeout=15)
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError("Screenshot was not produced; check Screen Recording permission")
            return {"status": "completed", "path": str(path)}
        if name == "open_app":
            subprocess.run(["open", "-a", args["app"]], check=True, timeout=10)
            return {"status": "completed", "app": args["app"]}
        url = args.get("url")
        if name in {"web_search", "weather"}:
            query = args.get("query", "weather " + args.get("location", ""))
            url = "https://www.google.com/search?" + urlencode({"q": query})
        subprocess.run(["open", "-a", "Safari", str(url)], check=True, timeout=10)
        return {
            "status": "completed",
            "opened_url": url,
            "note": "Browser opened; page content has not been read.",
        }

    def close(self):
        self.client.close()

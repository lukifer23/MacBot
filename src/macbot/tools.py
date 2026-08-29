"""A single tool registry. Side effects execute only through bound approvals."""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import psutil

from .auth import AuthStore
from .config import Settings

SCHEMAS: dict[str, tuple[str, dict[str, str]]] = {
    "local_time": ("Read the current date, time and UTC offset from this Mac's local clock", {}),
    "system_info": ("Read CPU, memory and disk usage on this Mac", {}),
    "rag_search": ("Search documents in the local knowledge base", {"query": "string"}),
    "open_app": ("Open one explicitly requested allowed application", {"app": "string"}),
    "web_search": (
        "Return structured external web results for an explicit search or current-information request",
        {"query": "string"},
    ),
    "browse_website": (
        "Open an explicitly requested public HTTP(S) website in the default browser",
        {"url": "string"},
    ),
    "screenshot": ("Save an explicitly requested screenshot locally", {}),
    "weather": (
        "Return structured current weather for a specified location",
        {"location": "string"},
    ),
}
READ_ONLY = {"system_info", "rag_search", "local_time", "web_search", "weather"}
# Only these implemented actions may opt into request-based execution. New tools
# do not inherit permission to change the desktop or create files.
AUTO_REQUESTED = {"open_app", "browse_website", "web_search", "weather", "screenshot"}
REQUESTED_SIDE_EFFECTS = {"open_app", "browse_website", "screenshot"}


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

    def requested(self, text: str) -> dict[str, dict[str, str]]:
        """Conservative request routing, never inferred from history/tool output.

        Values bind desktop targets verbatim. Empty constraints permit generated
        search queries, but only after an explicit search request. This is not an
        unrestricted language classifier: ambiguous phrasing gets clarification.
        """
        text = text.strip().replace("’", "'")
        if re.search(r"\b(?:don't|do not|never|without|instead of|not now)\b", text, re.I):
            return {}
        command = re.sub(
            r"^(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+))+", "", text, flags=re.I
        )
        selected: dict[str, dict[str, str]] = {}
        if re.fullmatch(
            r"(?:what(?:'s| is) (?:the )?(?:current |local )?(?:time|date)|what (?:time|day) is (?:it|now)|tell me (?:the )?(?:time|date))(?:\s+(?:now|today|here|on my mac))?[.!?]*",
            command,
            re.I,
        ):
            selected["local_time"] = {}
        opening = re.match(
            r"^(?:open|launch|start|bring up|visit)\s+(?:the\s+)?(.+)", command, re.I
        )
        if opening:
            target = opening[1]
            for app in self.settings.tools.allowed_apps:
                if re.match(re.escape(app) + r"(?:\b|$)", target, re.I):
                    selected["open_app"] = {"app": app}
                    break
            url = re.match(r"https?://[^\s<>\"']+", target, re.I)
            if url:
                selected["browse_website"] = {"url": url[0].rstrip(".,!?")}
        if re.match(
            r"^(?:take|capture|save)\s+(?:a\s+|an\s+)?(?:screenshot\b|image of (?:my |the )?(?:current )?(?:screen|desktop)\b)",
            command,
            re.I,
        ):
            selected["screenshot"] = {}
        searching = re.match(r"^(?:search|find|look up|look in|check)\b", command, re.I)
        if searching and re.search(r"\b(?:web|internet|online)\b", command, re.I):
            selected["web_search"] = {}
        if (
            re.search(r"\b(?:weather|forecast)\b", command, re.I)
            and (searching or re.match(r"^what(?:'s| is)\b", command, re.I))
            and re.search(r"\b(?:in|for|today|tomorrow|tonight|now)\b", command, re.I)
        ):
            selected.pop("web_search", None)
            selected["weather"] = {}
        if re.search(r"\b(?:documents|knowledge base|library)\b", command, re.I) and (
            searching or re.match(r"^(?:what|which|where|show|summarize)\b", command, re.I)
        ):
            selected["rag_search"] = {}
        if (
            re.match(r"^(?:show|check|how much|how full|what(?:'s| is))\b", command, re.I)
            and re.search(r"\b(?:cpu|memory|disk|system status)\b", command, re.I)
            and re.search(r"\b(?:usage|using|used|free|available|full|status)\b", command, re.I)
        ):
            selected["system_info"] = {}
        return {
            name: args for name, args in selected.items() if name in self.settings.tools.enabled
        }

    def validate_request(self, text: str, name: str, arguments: dict) -> None:
        self.validate(name, arguments)
        requested = self.requested(text)
        if name not in requested or any(arguments.get(k) != v for k, v in requested[name].items()):
            raise PermissionError("Tool action does not match the current explicit request")

    def authorize_planned(
        self, text: str, source_span: str, name: str, arguments: dict[str, Any]
    ) -> None:
        """Independently bind a semantic plan to exact current-message evidence."""
        self.validate(name, arguments)
        if not source_span or source_span not in text:
            raise PermissionError("Action is not grounded in the current request")
        evidence = source_span.casefold()
        if re.search(r"\b(?:don't|do not|never|without)\b", evidence):
            raise PermissionError("Negated actions cannot execute")
        required: dict[str, tuple[str, ...]] = {
            "local_time": ("time", "date", "day", "clock"),
            "system_info": ("cpu", "memory", "disk", "system"),
            "rag_search": ("document", "documents", "library", "knowledge"),
            "web_search": ("search", "web", "internet", "online", "latest", "current"),
            "weather": ("weather", "forecast", "temperature", "rain"),
            "open_app": ("open", "launch", "start", "bring up"),
            "browse_website": ("open", "visit", "browse"),
            "screenshot": ("screenshot", "capture", "screen"),
        }
        if not any(token in evidence for token in required[name]):
            raise PermissionError("Action evidence does not express the requested capability")
        if name == "open_app" and arguments["app"].casefold() not in evidence:
            raise PermissionError("Application target is not present in the request")
        if name == "browse_website" and arguments["url"].casefold() not in text.casefold():
            raise PermissionError("Website target is not present in the request")

    def definitions(self, text: str | None = None, used: set[str] | None = None) -> list[dict]:
        requested = self.requested(text) if text is not None else None
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
            and (requested is None or name in requested)
            and name not in (used or set())
        ]
        for definition in definitions:
            function = definition["function"]
            if self.settings.tools.auto_run_requested and function["name"] in AUTO_REQUESTED:
                function["description"] = (
                    function["description"]
                    .replace(", after user confirmation", "")
                    .replace("Requires user confirmation.", "")
                    .replace("after user confirmation", "when explicitly requested")
                    + " Runs automatically for the current explicit request; do not ask again."
                )
            if function["name"] == "open_app":
                function["parameters"]["properties"]["app"]["enum"] = list(
                    self.settings.tools.allowed_apps
                )
            if requested is not None:
                for key, value in requested[function["name"]].items():
                    function["parameters"]["properties"][key]["enum"] = [value]
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
        if name == "local_time":
            now = datetime.now().astimezone()
            return {"datetime": now.isoformat(), "timezone": now.tzname(), "source": "mac_clock"}
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
        if name == "web_search":
            return self._web_search(args["query"])
        if name == "weather":
            return self._weather(args["location"])
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
        url = args["url"]
        subprocess.run(["open", str(url)], check=True, timeout=10)
        return {
            "status": "completed",
            "opened_url": url,
            "note": "Opened in the default browser.",
        }

    @staticmethod
    def _keychain_secret(service: str) -> str | None:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    def _web_search(self, query: str) -> dict[str, Any]:
        key = self._keychain_secret("local.macbot.brave-search")
        if key:
            response = self.client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 5, "safesearch": "moderate"},
                headers={"Accept": "application/json", "X-Subscription-Token": key},
            )
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise RuntimeError("Search response exceeded the configured limit")
            rows = response.json().get("web", {}).get("results", [])
            results = [
                {
                    "title": row.get("title", ""),
                    "url": row.get("url", ""),
                    "snippet": row.get("description", ""),
                }
                for row in rows[:5]
                if isinstance(row, dict) and row.get("url")
            ]
            if not results:
                return {"status": "empty", "provider": "brave", "query": query, "results": []}
            return {"status": "completed", "provider": "brave", "query": query, "results": results}
        try:
            from ddgs import DDGS

            rows = list(DDGS(timeout=8).text(query, max_results=5))
        except Exception as exc:
            raise RuntimeError(
                "Web search is unavailable: configure Brave Search or retry the no-key provider"
            ) from exc
        results = [
            {
                "title": row.get("title", ""),
                "url": row.get("href", ""),
                "snippet": row.get("body", ""),
            }
            for row in rows
            if isinstance(row, dict) and row.get("href")
        ]
        return {
            "status": "completed" if results else "empty",
            "provider": "ddgs",
            "degraded": True,
            "query": query,
            "results": results,
        }

    def _weather(self, location: str) -> dict[str, Any]:
        geocode = self.client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        geocode.raise_for_status()
        if len(geocode.content) > 1_000_000:
            raise RuntimeError("Weather location response exceeded the configured limit")
        matches = geocode.json().get("results") or []
        if not matches:
            return {"status": "empty", "provider": "open-meteo", "location": location}
        place = matches[0]
        forecast = self.client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "auto",
            },
        )
        forecast.raise_for_status()
        if len(forecast.content) > 1_000_000:
            raise RuntimeError("Weather response exceeded the configured limit")
        return {
            "status": "completed",
            "provider": "open-meteo",
            "location": {
                "name": place.get("name"),
                "admin1": place.get("admin1"),
                "country": place.get("country"),
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
            },
            "current": forecast.json().get("current", {}),
            "units": forecast.json().get("current_units", {}),
        }

    def close(self):
        self.client.close()

"""A single tool registry. Side effects execute only through bound approvals."""

from __future__ import annotations

import re
import secrets
import subprocess
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
REQUESTED_SIDE_EFFECTS = {"open_app", "browse_website", "screenshot"}


class Tools:
    def __init__(self, settings: Settings, auth: AuthStore):
        self.settings, self.auth = settings, auth
        self.client = httpx.Client(timeout=8, trust_env=False)

    def requested(self, text: str) -> dict[str, dict[str, str]]:
        """Conservative request routing, never inferred from history/tool output.

        Values bind desktop targets verbatim. Empty constraints permit generated
        search queries, but only after an explicit search request. This is not an
        unrestricted language classifier: ambiguous phrasing gets clarification.
        """
        text = text.strip().replace("’", "'")
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
            location = re.search(r"\b(?:in|for)\s+([^.!?]+)", command, re.I)
            selected["weather"] = {
                "location": location.group(1).strip() if location else "current location"
            }
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
        negative_targets = {
            "local_time": r"(?:time|date|clock)",
            "system_info": r"(?:cpu|memory|disk|system)",
            "rag_search": r"(?:documents|library|knowledge base)",
            "web_search": r"(?:web|internet|online|web search)",
            "weather": r"(?:weather|forecast)",
            "open_app": r"(?:open|launch|start|bring up)",
            "browse_website": r"(?:open|visit|browse)",
            "screenshot": r"(?:screenshot|capture(?: the)? screen)",
        }
        for name, target in negative_targets.items():
            if re.search(
                rf"\b(?:don't|do not|never|without|instead of)\b[^.!?]{{0,80}}{target}",
                command,
                re.I,
            ):
                selected.pop(name, None)
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
        negative_targets = {
            "local_time": r"(?:time|date|clock)",
            "system_info": r"(?:cpu|memory|disk|system)",
            "rag_search": r"(?:(?:search|look in|check)\s+(?:my\s+)?(?:documents|library|knowledge base))",
            "web_search": r"(?:(?:search|look up|check)(?:ing)?\s+(?:the\s+)?(?:web|internet|online)|web search)",
            "weather": r"(?:weather|forecast|temperature)",
            "open_app": r"(?:open|launch|start|bring up)",
            "browse_website": r"(?:open|visit|browse)",
            "screenshot": r"(?:screenshot|capture(?: the)? screen)",
        }
        if re.search(
            rf"\b(?:don't|do not|never|without)\b[^.!?]{{0,60}}{negative_targets[name]}",
            text,
            re.I,
        ):
            raise PermissionError("The current request explicitly negates this action")
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
            actual = (
                sorted(arguments) if isinstance(arguments, dict) else [type(arguments).__name__]
            )
            expected = sorted(SCHEMAS[name][1])
            raise ValueError(
                f"Tool arguments for {name} do not match its schema; "
                f"expected keys {expected}, got {actual}"
            )
        if any(not isinstance(v, str) or len(v) > 2000 or "\x00" in v for v in arguments.values()):
            raise ValueError("Invalid tool argument value")
        if name == "open_app" and arguments["app"] not in self.settings.tools.allowed_apps:
            raise PermissionError("Application is not allowed")
        if name == "browse_website":
            u = urlsplit(arguments["url"])
            if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
                raise ValueError("A complete HTTP(S) URL without credentials is required")
        return dict(arguments)

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
        if not key:
            return {
                "status": "not_configured",
                "provider": "brave",
                "query": query,
                "results": [],
                "message": "Configure a Brave Search credential in MacBot settings.",
            }
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
        return {
            "status": "completed" if results else "empty",
            "provider": "brave",
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

"""Utility tools used by MacBot.

This module contains a collection of helper functions that allow the assistant
to interact with the host system.  Each tool is accompanied by a lightweight
schema describing its parameters and the permission key required for its
execution.  Other modules can expose this schema to the language model to
enable LLM function calling.  Before any tool is executed, its permission key
is checked against the ``tools.enabled`` list in :mod:`config`.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import psutil
import requests

from . import config as cfg


# ---------------------------------------------------------------------------
# Tool function implementations


def web_search(query: str) -> str:
    """Open the default browser and search the web for ``query``."""

    query = (query or "").strip()
    if not query:
        return "No search query provided."
    engine = "google"
    base = (
        "https://www.google.com/search?q="
        if engine == "google"
        else "https://duckduckgo.com/?q="
    )
    url = f"{base}{query.replace(' ', '+')}"
    try:
        subprocess.run(["open", "-a", "Safari", url], check=True)
        return f"Opened Safari to search for '{query}'."
    except Exception as e:  # pragma: no cover - platform dependent
        return f"Web search failed: {e}"


def browse_website(url: str) -> str:
    """Open ``url`` in the default browser."""

    url = (url or "").strip()
    if not url:
        return "No URL provided."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        subprocess.run(["open", "-a", "Safari", url], check=True)
        return f"Opened {url} in Safari."
    except Exception as e:  # pragma: no cover - platform dependent
        return f"Website open failed: {e}"


def get_system_info() -> str:
    """Return a snapshot of CPU, memory and disk usage."""

    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        return f"System Status: CPU {cpu}%, RAM {mem}%, Disk {disk}%"
    except Exception as e:  # pragma: no cover - unlikely to fail
        return f"System info failed: {e}"


def open_app(app_name: str) -> str:
    """Open an application by name."""

    app_name = (app_name or "").strip()
    if not app_name:
        return "No application name provided."
    allowed = set(a.lower() for a in cfg.get_allowed_apps())

    alias = {
        "safari": "Safari",
        "chrome": "Google Chrome",
        "finder": "Finder",
        "terminal": "Terminal",
        "mail": "Mail",
        "messages": "Messages",
        "facetime": "FaceTime",
        "photos": "Photos",
        "music": "Music",
        "calendar": "Calendar",
        "notes": "Notes",
        "calculator": "Calculator",
    }

    name_lower = app_name.lower()
    pretty = alias.get(name_lower, app_name)
    if allowed and pretty.lower() not in allowed:
        return f"Opening '{pretty}' is not allowed by policy."
    try:
        subprocess.run(["open", "-a", pretty], check=True)
        return f"Opened {pretty}."
    except Exception as e:  # pragma: no cover - platform dependent
        return f"Failed to open {pretty}: {e}"


def take_screenshot() -> str:
    """Capture a screenshot to the configured directory."""

    try:
        ts = int(time.time())
        filename = f"screenshot_{ts}.png"
        save_dir = os.path.expanduser(cfg.get("tools.screenshot.save_path", "~/Desktop"))
        path = os.path.join(save_dir, filename)
        subprocess.run(["screencapture", path], check=True)
        return f"Saved screenshot to {path}"
    except Exception as e:  # pragma: no cover - platform dependent
        return f"Screenshot failed: {e}"


def get_weather(location: Optional[str] = None) -> str:
    """Retrieve the weather for ``location`` using a web search."""

    try:
        loc = (location or cfg.get("tools.weather.default_location", "")).strip()
        q = f"weather {loc}" if loc else "weather"
        return web_search(q)
    except Exception as e:  # pragma: no cover - network dependent
        return f"Weather lookup failed: {e}"


def rag_search(query: str, n_results: int = 3) -> str:
    """Query the local RAG knowledge base."""

    base = cfg.get_rag_base_url()
    try:
        try:
            requests.get(f"{base}/health", timeout=2)
        except Exception:
            return "Knowledge base is unavailable"
        r = requests.post(f"{base}/api/search", json={"query": query}, timeout=8)
        if r.status_code != 200:
            return f"Knowledge base search failed: HTTP {r.status_code}"
        data = r.json()
        results = data.get("results", [])
        if not results:
            return "No relevant information found in the knowledge base."
        lines = []
        for i, res in enumerate(results[:n_results]):
            title = (res.get("metadata", {}) or {}).get("title", f"Result {i+1}")
            content = (res.get("content") or "").strip().replace("\n", " ")
            lines.append(f"{i+1}. {title}: {content[:180]}...")
        return "Top results:\n" + "\n".join(lines)
    except Exception as e:  # pragma: no cover - network dependent
        return f"Knowledge base search failed: {e}"


# ---------------------------------------------------------------------------
# Tool registry and helper functions


@dataclass
class ToolSpec:
    """Metadata describing an executable tool."""

    name: str
    func: Callable[..., str]
    description: str
    parameters: Dict[str, Any]
    permission: str


# Registry of available tools
TOOL_REGISTRY: Dict[str, ToolSpec] = {
    "web_search": ToolSpec(
        name="web_search",
        func=web_search,
        description="Search the web using the default browser.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"],
        },
        permission="web_search",
    ),
    "browse_website": ToolSpec(
        name="browse_website",
        func=browse_website,
        description="Open a URL in the default browser.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Website URL"}
            },
            "required": ["url"],
        },
        permission="browse_website",
    ),
    "get_system_info": ToolSpec(
        name="get_system_info",
        func=get_system_info,
        description="Return CPU, RAM and disk utilisation.",
        parameters={"type": "object", "properties": {}},
        permission="system_monitor",
    ),
    "open_app": ToolSpec(
        name="open_app",
        func=open_app,
        description="Open an application by name.",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Application name",
                }
            },
            "required": ["app_name"],
        },
        permission="app_launcher",
    ),
    "take_screenshot": ToolSpec(
        name="take_screenshot",
        func=take_screenshot,
        description="Capture a screenshot to the configured directory.",
        parameters={"type": "object", "properties": {}},
        permission="screenshot",
    ),
    "get_weather": ToolSpec(
        name="get_weather",
        func=get_weather,
        description="Get the weather for an optional location.",
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Location name",
                }
            },
            "required": [],
        },
        permission="weather",
    ),
    "rag_search": ToolSpec(
        name="rag_search",
        func=rag_search,
        description="Search the local knowledge base.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 3,
                },
            },
            "required": ["query"],
        },
        permission="rag_search",
    ),
}


def get_tool_schema() -> List[Dict[str, Any]]:
    """Return tool specifications formatted for OpenAI function calling."""

    schema: List[Dict[str, Any]] = []
    for spec in TOOL_REGISTRY.values():
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
        )
    return schema


def execute_tool(name: str, **kwargs: Any) -> str:
    """Execute ``name`` with ``kwargs`` if permitted by configuration."""

    enabled = {t.lower() for t in cfg.get_enabled_tools()}
    spec = TOOL_REGISTRY.get(name)
    if not spec:
        return f"Tool '{name}' is not recognized."
    if spec.permission.lower() not in enabled:
        return f"Tool '{name}' is disabled by configuration."
    try:
        return spec.func(**kwargs)
    except TypeError as e:
        return f"Invalid arguments for tool '{name}': {e}"


__all__ = [
    "web_search",
    "browse_website",
    "get_system_info",
    "open_app",
    "take_screenshot",
    "get_weather",
    "rag_search",
    "TOOL_REGISTRY",
    "get_tool_schema",
    "execute_tool",
]


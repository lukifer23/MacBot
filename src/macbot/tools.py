"""
Shared tool functions for MacBot across modules (voice assistant, web dashboard).

All functions are thin, side-effecting wrappers around macOS capabilities.
They use config to enforce policies like allowed applications.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import List, Optional

import psutil
import requests
from urllib.parse import quote, urlsplit, urlunsplit

from . import config as cfg


def web_search(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "No search query provided."
    engine = "google"
    base = "https://www.google.com/search?q=" if engine == "google" else "https://duckduckgo.com/?q="
    url = f"{base}{quote(query)}"
    try:
        subprocess.run(["open", "-a", "Safari", url], check=True)
        return f"Opened Safari to search for '{query}'."
    except Exception as e:
        return f"Web search failed: {e}"


def browse_website(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "No URL provided."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parts = urlsplit(url)
    if not parts.netloc:
        return "Invalid URL."

    path = quote(parts.path)
    query = quote(parts.query, safe="=&")
    fragment = quote(parts.fragment)
    normalized = urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
    try:
        subprocess.run(["open", "-a", "Safari", normalized], check=True)
        return f"Opened {normalized} in Safari."
    except Exception as e:
        return f"Website open failed: {e}"


def get_system_info() -> str:
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        return f"System Status: CPU {cpu}%, RAM {mem}%, Disk {disk}%"
    except Exception as e:
        return f"System info failed: {e}"


def open_app(app_name: str) -> str:
    app_name = (app_name or "").strip()
    if not app_name:
        return "No application name provided."
    allowed = set(a.lower() for a in cfg.get_allowed_apps())
    pretty = app_name
    # map lowercase direct names to proper names when possible
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
    except Exception as e:
        return f"Failed to open {pretty}: {e}"


def take_screenshot() -> str:
    try:
        ts = int(time.time())
        filename = f"screenshot_{ts}.png"
        save_dir = os.path.expanduser(cfg.get("tools.screenshot.save_path", "~/Desktop"))
        path = os.path.join(save_dir, filename)
        subprocess.run(["screencapture", path], check=True)
        return f"Saved screenshot to {path}"
    except Exception as e:
        return f"Screenshot failed: {e}"


def get_weather(location: Optional[str] = None) -> str:
    try:
        # On macOS, open weather in Safari for the location
        loc = (location or cfg.get("tools.weather.default_location", "")).strip()
        q = f"weather {loc}" if loc else "weather"
        return web_search(q)
    except Exception as e:
        return f"Weather lookup failed: {e}"


def rag_search(query: str, n_results: int = 3) -> str:
    base = cfg.get_rag_base_url()
    try:
        # quick health check
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
    except Exception as e:
        return f"Knowledge base search failed: {e}"


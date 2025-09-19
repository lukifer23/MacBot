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

from . import config as cfg


def _get_rag_auth_token() -> Optional[str]:
    """Return the first configured RAG API token, if present."""

    try:
        tokens = cfg.get_rag_api_tokens()
    except Exception:
        return None

    if not tokens:
        return None

    for token in tokens:
        if not token:
            continue
        token_str = str(token).strip()
        if token_str and token_str.lower() != "change-me":
            return token_str

    return None


def web_search(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "No search query provided."
    engine = "google"
    base = "https://www.google.com/search?q=" if engine == "google" else "https://duckduckgo.com/?q="
    url = f"{base}{query.replace(' ', '+')}"
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
    try:
        subprocess.run(["open", "-a", "Safari", url], check=True)
        return f"Opened {url} in Safari."
    except Exception as e:
        return f"Website open failed: {e}"


def get_system_info() -> str:
    try:
        # Use non-blocking call to get instantaneous CPU usage
        cpu = psutil.cpu_percent(interval=None)
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
    token = _get_rag_auth_token()
    headers = None
    if token:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-API-Token": token,
        }
    try:
        # quick health check
        try:
            requests.get(f"{base}/health", timeout=2, headers=headers)
        except Exception:
            return "Knowledge base is unavailable"
        r = requests.post(
            f"{base}/api/search",
            json={"query": query},
            timeout=8,
            headers=headers,
        )
        if token and r.status_code in {401, 403}:
            detail = ""
            try:
                payload = r.json()
                if isinstance(payload, dict):
                    detail = payload.get("detail") or payload.get("message") or payload.get("error") or ""
                elif isinstance(payload, list) and payload:
                    detail = str(payload[0])
            except Exception:
                detail = (r.text or "").strip()

            detail = (detail or "").strip()
            if not detail:
                detail = (r.text or "").strip()
            if detail:
                return f"Knowledge base authentication failed (HTTP {r.status_code}): {detail}"
            return (
                f"Knowledge base authentication failed (HTTP {r.status_code}). "
                "Please check the configured API token."
            )
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


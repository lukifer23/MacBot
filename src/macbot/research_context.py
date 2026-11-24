"""Compact recorded research evidence for synthesis and source inspection."""

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit


def research_sources(steps: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        result = step.get("result")
        if step["state"] != "succeeded" or not isinstance(result, dict):
            continue
        tool = step["tool"]
        rows = [result.get("evidence")] if tool == "web_fetch" else result.get("results")
        if not isinstance(rows, list):
            continue
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            provenance = row.get("provenance") if tool == "rag_search" else None
            if isinstance(provenance, dict):
                identifier = row.get("id")
                title = provenance.get("title") or "Local document"
                text = row.get("content")
                url = None
            elif tool in {"web_search", "web_fetch"}:
                identifier = url = row.get("url")
                if not isinstance(url, str) or len(url) > 2048:
                    continue
                try:
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme not in {"https", "http"}
                        or not parsed.hostname
                        or parsed.username is not None
                        or parsed.password is not None
                    ):
                        continue
                except ValueError:
                    continue
                title = row.get("title") or parsed.hostname
                text = row.get("excerpt") if tool == "web_fetch" else row.get("snippet")
            else:
                continue
            if not isinstance(identifier, str) or not identifier:
                continue
            key = (str(tool), identifier)
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "id": f"S{len(sources) + 1}",
                    "title": str(title)[:160],
                    "url": url,
                    "excerpt": str(text or "")[:480],
                }
            )
    return sources

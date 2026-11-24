"""Compact recorded research evidence for synthesis and source inspection."""

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit


def fit_research_sources(
    sources: Sequence[dict[str, Any]], character_budget: int
) -> list[dict[str, Any]]:
    """Fit a serialized prefix while leaving URLs in the recorded result."""
    selected: list[dict[str, Any]] = []
    remaining = character_budget - 2
    for source in sources:
        entry = {key: value for key, value in source.items() if key != "url"}
        size = len(json.dumps(entry, ensure_ascii=False)) + (2 if selected else 0)
        if size <= remaining:
            selected.append(entry)
            remaining -= size
    return selected


def replan_context(observations: Sequence[dict[str, Any]], character_budget: int) -> str:
    steps = [{"tool": item["capability"], **item} for item in observations]
    outcomes = [
        {
            "tool": item["tool"],
            "state": item["state"],
            "outcome": (item.get("result") or {}).get("status"),
            "failure_class": item.get("failure_class"),
            "attempted_arguments": {
                key: str(value)[:128] for key, value in item["arguments"].items()
            },
        }
        for item in steps
    ]
    payload: dict[str, Any] = {"outcomes": [], "sources": []}
    for outcome in outcomes:
        candidate = {**payload, "outcomes": [*payload["outcomes"], outcome]}
        if len(json.dumps(candidate, ensure_ascii=False)) > character_budget * 2 // 3:
            break
        payload = candidate
    payload["sources"] = fit_research_sources(
        research_sources(steps), character_budget + 2 - len(json.dumps(payload, ensure_ascii=False))
    )
    return json.dumps(payload, ensure_ascii=False)


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

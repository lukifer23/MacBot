"""The single bounded capability registry for the research-only release."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import subprocess
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .auth import AuthStore
from .config import Settings

SCHEMAS: dict[str, tuple[str, dict[str, str]]] = {
    "rag_search": ("Search documents in the local knowledge base", {"query": "string"}),
    "web_search": (
        "Return structured external web results for an explicit search or current-information request",
        {"query": "string"},
    ),
    "web_fetch": (
        "Fetch bounded text evidence from one explicit public HTTP(S) search result",
        {"url": "string"},
    ),
}
READ_ONLY = {"rag_search", "web_search", "web_fetch"}
TASK_RELEASE_CAPABILITIES = frozenset({"rag_search", "web_search", "web_fetch"})


class _TextExtractor(HTMLParser):
    """Small deterministic HTML text extractor; scripts and styles are never evidence."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


class Tools:
    def __init__(self, settings: Settings, auth: AuthStore):
        self.settings, self.auth = settings, auth
        self.client = httpx.Client(timeout=8, trust_env=False)

    def requested(self, text: str) -> dict[str, dict[str, str]]:
        """Route only explicit research requests; broader Mac actions are not capabilities."""
        text = text.strip().replace("’", "'")
        command = re.sub(
            r"^(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+))+", "", text, flags=re.I
        )
        selected: dict[str, dict[str, str]] = {}
        searching = re.match(r"^(?:search|find|look up|look in|check)\b", command, re.I)
        if searching and re.search(r"\b(?:web|internet|online)\b", command, re.I):
            selected["web_search"] = {}
        if re.search(r"\b(?:documents|knowledge base|library)\b", command, re.I) and (
            searching or re.match(r"^(?:what|which|where|show|summarize)\b", command, re.I)
        ):
            selected["rag_search"] = {}
        fetch = re.search(r"https?://[^\s<>\"']+", command, re.I)
        if fetch and re.match(r"^(?:fetch|read|extract|inspect|summarize)\b", command, re.I):
            selected["web_fetch"] = {"url": fetch[0].rstrip(".,!?")}
        negative_targets = {
            "rag_search": r"(?:documents|library|knowledge base)",
            "web_search": r"(?:web|internet|online|web search)",
            "web_fetch": r"(?:fetch|read|extract|inspect|summarize)",
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
        requested = self.requested(text)
        if name not in requested or any(arguments.get(k) != v for k, v in requested[name].items()):
            raise PermissionError("Tool action does not match the current explicit request")
        self.validate(name, arguments)

    def authorize_planned(
        self, text: str, source_span: str, name: str, arguments: dict[str, Any]
    ) -> None:
        """Independently bind a semantic plan to exact current-message evidence."""
        self.validate(name, arguments)
        if not source_span or source_span not in text:
            raise PermissionError("Action is not grounded in the current request")
        evidence = source_span.casefold()
        required: dict[str, tuple[str, ...]] = {
            "rag_search": ("document", "documents", "library", "knowledge"),
            "web_search": ("search", "web", "internet", "online", "latest", "current"),
            "web_fetch": ("fetch", "read", "extract", "inspect", "summarize"),
        }
        negative_targets = {
            "rag_search": r"(?:(?:search|look in|check)\s+(?:my\s+)?(?:documents|library|knowledge base))",
            "web_search": r"(?:(?:search|look up|check)(?:ing)?\s+(?:the\s+)?(?:web|internet|online)|web search)",
            "web_fetch": r"(?:fetch|read|extract|inspect|summarize)",
        }
        if re.search(
            rf"\b(?:don't|do not|never|without)\b[^.!?]{{0,60}}{negative_targets[name]}",
            text,
            re.I,
        ):
            raise PermissionError("The current request explicitly negates this action")
        if not any(token in evidence for token in required[name]):
            raise PermissionError("Action evidence does not express the requested capability")
        if name == "web_fetch" and arguments["url"].casefold() not in text.casefold():
            raise PermissionError("Evidence URL is not present in the request")

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
        if name == "web_fetch":
            self._validate_public_url(arguments["url"])
        return dict(arguments)

    def read(self, name: str, arguments: dict) -> dict:
        if name not in READ_ONLY:
            raise PermissionError("Explicit approval required")
        return self._execute(name, arguments)

    def _execute(self, name: str, arguments: dict) -> dict:
        args = self.validate(name, arguments)
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
        if name == "web_fetch":
            return self._web_fetch(args["url"])
        raise PermissionError("Capability is not available in the research release")

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

    @staticmethod
    def _validate_public_url(url: str) -> str:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in {None, 80, 443}
        ):
            raise ValueError("A public HTTP(S) URL without credentials or fragments is required")
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
                )
            }
        except socket.gaierror as exc:
            raise ValueError("Web source hostname could not be resolved") from exc
        if not addresses or any(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            for address in addresses
        ):
            raise PermissionError("Web sources must resolve only to public addresses")
        return url

    def _web_fetch(self, url: str) -> dict[str, Any]:
        current = self._validate_public_url(url)
        response: httpx.Response | None = None
        for _ in range(4):
            response = self.client.get(
                current,
                follow_redirects=False,
                headers={"Accept": "text/html,text/plain,application/json;q=0.8"},
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location:
                raise RuntimeError("Web source redirect omitted its destination")
            current = self._validate_public_url(urljoin(current, location))
        else:
            raise RuntimeError("Web source exceeded the redirect limit")
        assert response is not None
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"text/html", "text/plain", "application/json"}:
            raise ValueError(f"Unsupported web evidence content type: {content_type or 'unknown'}")
        body = response.content
        if len(body) > 2_000_000:
            raise RuntimeError("Web evidence exceeded the 2 MB limit")
        decoded = response.text
        if content_type == "text/html":
            parser = _TextExtractor()
            parser.feed(decoded)
            text = parser.text()
        else:
            text = re.sub(r"\s+", " ", decoded).strip()
        excerpt = text[:12_000]
        return {
            "status": "completed" if excerpt else "empty",
            "evidence": {
                "source_kind": "web",
                "source_id": current,
                "url": current,
                "title": "",
                "retrieved_at": time.time_ns(),
                "excerpt": excerpt,
                "body_hash": hashlib.sha256(body).hexdigest(),
                "content_type": content_type,
            },
        }

    def close(self):
        self.client.close()

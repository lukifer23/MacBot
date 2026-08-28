#!/usr/bin/env python3
"""Offline LLM candidate screening. No tools execute; outputs are retained for review.

This measures text generation, NOT end-to-audible latency or device acceptance.
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import socket
import tempfile
import threading
import time
from pathlib import Path

import psutil

from macbot.config import Settings, load, prepare
from macbot.llm import LocalLLM
from macbot.orchestrator import MacBotOrchestrator
from macbot.provision import catalog, sha256, verify
from macbot.tools import Tools

CASES = [
    ("What is two plus two? Answer with the number.", None, None, "4"),
    ("What is the capital of France? Answer briefly.", None, None, "paris"),
    ("What is 7 multiplied by 6? Answer with the number.", None, None, "42"),
    ("Say hello.", None, None, "hello"),
    ("Explain what a microphone does in one sentence.", None, None, "sound"),
    ("Name the largest planet in our solar system.", None, None, "jupiter"),
    ("Open Calculator.", "open_app", {"app": "Calculator"}, None),
    ("Launch Notes.", "open_app", {"app": "Notes"}, None),
    ("Open Finder.", "open_app", {"app": "Finder"}, None),
    ("Launch Safari.", "open_app", {"app": "Safari"}, None),
    ("Take a screenshot.", "screenshot", {}, None),
    ("Save a screenshot of my screen.", "screenshot", {}, None),
    ("Show current CPU, memory and disk usage.", "system_info", {}, None),
    ("How much memory is this Mac using right now?", "system_info", {}, None),
    ("Search my local documents for the warranty.", "rag_search", None, None),
    ("Find the installation guide in my knowledge base.", "rag_search", None, None),
    ("Search the web for local speech recognition.", "web_search", None, None),
    ("Find apple pie recipes on the web.", "web_search", None, None),
    (
        "Open https://example.org in the browser.",
        "browse_website",
        {"url": "https://example.org"},
        None,
    ),
    ("Check the weather in Chicago.", "weather", None, None),
]


HOLDOUT_CASES = [
    ("How many millimeters are in ten centimeters?", None, None, ("100", "hundred")),
    ("Which weekday comes immediately after Tuesday?", None, None, "wednesday"),
    ("What is the freezing point of water in Celsius?", None, None, ("0", "zero")),
    ("What is the opposite of hot?", None, None, "cold"),
    ("Name the largest ocean on Earth.", None, None, "pacific"),
    ("What is twelve divided by three?", None, None, ("4", "four")),
    ("How many sides does a triangle have?", None, None, ("3", "three")),
    ("What do bees make? Answer in one sentence.", None, None, "honey"),
    (
        "Do not open Calculator. Just explain what it does.",
        None,
        None,
        ("math", "arithmetic", "calculations"),
    ),
    (
        "Do not search online. Explain what a web browser is.",
        None,
        None,
        ("websites", "web", "internet"),
    ),
    ("Please open the Calculator app for me.", "open_app", {"app": "Calculator"}, None),
    ("Can you launch Notes on my Mac?", "open_app", {"app": "Notes"}, None),
    ("Bring up Finder so I can look at my files.", "open_app", {"app": "Finder"}, None),
    ("Start the Safari app.", "open_app", {"app": "Safari"}, None),
    ("Please capture a screenshot now.", "screenshot", {}, None),
    ("Save an image of my current desktop.", "screenshot", {}, None),
    ("Check the current memory usage on this computer.", "system_info", {}, None),
    ("Show this Mac's CPU usage right now.", "system_info", {}, None),
    ("Check how full my disk is.", "system_info", {}, None),
    ("Look in my imported documents for the return policy.", "rag_search", None, None),
    (
        "Search the local knowledge base for battery replacement instructions.",
        "rag_search",
        None,
        None,
    ),
    ("Find passages about invoice payment in my documents.", "rag_search", None, None),
    ("Please search the internet for sourdough recipes.", "web_search", None, None),
    ("Look up beginner guitar lessons on the web.", "web_search", None, None),
    ("Search online for the latest Apple news.", "web_search", None, None),
    (
        "Open https://example.com in my browser.",
        "browse_website",
        {"url": "https://example.com"},
        None,
    ),
    (
        "Visit https://www.wikipedia.org in Safari.",
        "browse_website",
        {"url": "https://www.wikipedia.org"},
        None,
    ),
    ("Check today's weather in Boston.", "weather", None, None),
    ("What's the forecast in Seattle?", "weather", None, None),
    ("Look up the weather for Austin, Texas.", "weather", None, None),
]


def contains_answer(content, term):
    terms = (
        term
        if isinstance(term, tuple)
        else {"4": ("4", "four"), "42": ("42", "forty-two", "forty two")}.get(term, (term,))
    )
    return any(re.search(r"\b" + re.escape(word) + r"\b", content, re.IGNORECASE) for word in terms)


def percentile(values, p=0.95):
    return sorted(values)[max(0, __import__("math").ceil(len(values) * p) - 1)]


def run(name, backend, destination, case_set="core"):
    cases = HOLDOUT_CASES if case_set == "holdout" else CASES
    if destination.exists():
        raise FileExistsError("Choose a new output path; benchmark evidence is immutable")
    root = load().data_dir
    receipt = verify(load(), name)
    with tempfile.TemporaryDirectory(prefix="macbot-bench-") as temporary:
        s = Settings(data_dir=Path(temporary))
        prepare(s)
        # Model directories are read-only shared artifacts, never substitute implementations.
        (s.data_dir / "models" / name).symlink_to(root / "models" / name, target_is_directory=True)
        (s.data_dir / "bin").symlink_to(root / "bin", target_is_directory=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        s.models.llm = name
        s.models.llm_backend = backend
        s.models.llm_url = f"http://127.0.0.1:{port}"
        s.models.temperature = 0
        s.models.max_tokens = 128
        supervisor = MacBotOrchestrator(s)
        llm = None
        tools = None
        started = time.monotonic()
        try:
            if backend == "llama":
                supervisor.definitions()
                outcome = supervisor.start_service(
                    supervisor.service_definitions["llm"], retries=120
                )
                if not outcome["success"]:
                    raise RuntimeError(outcome)
            llm = LocalLLM(s, supervisor.auth)
            tools = Tools(s, supervisor.auth)
            startup = time.monotonic() - started
            records = []
            # First pass includes one cold inference; second pass is warm. No history/cache shortcut is requested.
            for repeat in range(2):
                for index, (prompt, expected, args, term) in enumerate(cases):
                    start = time.monotonic()
                    first = None
                    content = ""
                    calls = {}
                    error = None
                    try:
                        for delta in llm.stream(
                            [
                                {"role": "system", "content": s.system_prompt},
                                {"role": "user", "content": prompt},
                            ],
                            tools.definitions(),
                            threading.Event(),
                        ):
                            if (
                                delta.get("content")
                                or delta.get("tool_calls")
                                or delta.get("_first_token")
                            ) and first is None:
                                first = time.monotonic() - start
                            content += delta.get("content") or ""
                            for fragment in delta.get("tool_calls", []):
                                call = calls.setdefault(
                                    fragment.get("index", 0), {"name": "", "arguments": ""}
                                )
                                for key in ("name", "arguments"):
                                    call[key] += fragment.get("function", {}).get(key, "")
                        parsed = [
                            {"name": c["name"], "arguments": json.loads(c["arguments"])}
                            for c in calls.values()
                        ]
                        for call in parsed:
                            tools.validate(call["name"], call["arguments"])
                        passed = (
                            (not parsed and contains_answer(content, term))
                            if expected is None
                            else (
                                len(parsed) == 1
                                and parsed[0]["name"] == expected
                                and (args is None or parsed[0]["arguments"] == args)
                            )
                        )
                    except Exception as exc:
                        parsed = []
                        passed = False
                        error = f"{type(exc).__name__}: {exc}"
                    processes = [psutil.Process()]
                    if backend == "llama":
                        processes.append(psutil.Process(supervisor.processes["llm"].pid))
                    record = {
                        "case": index,
                        "repeat": repeat,
                        "cold": repeat == 0 and index == 0,
                        "prompt": prompt,
                        "content": content,
                        "calls": parsed,
                        "pass": passed,
                        "error": error,
                        "ttft_ms": first * 1000 if first is not None else None,
                        "total_ms": (time.monotonic() - start) * 1000,
                        "rss_bytes": sum(p.memory_info().rss for p in processes),
                    }
                    records.append(record)
                    with destination.open("a") as output:
                        output.write(
                            json.dumps({"model": name, "backend": backend, **record}) + "\n"
                        )
                    print(
                        name,
                        repeat,
                        index,
                        "PASS" if passed else "FAIL",
                        record["ttft_ms"],
                        flush=True,
                    )
            warm = [r for r in records if r["repeat"] == 1]
            summary = {
                "model": receipt,
                "case_set": case_set,
                "cases": len(cases),
                "artifacts": catalog()[name],
                "runtime_versions": {
                    package: importlib.metadata.version(package)
                    for package in (
                        ("macbot", "httpx", "mlx-lm") if backend == "mlx" else ("macbot", "httpx")
                    )
                },
                "binary_sha256": sha256(root / "bin/llama-server") if backend == "llama" else None,
                "system_prompt_sha256": hashlib.sha256(s.system_prompt.encode()).hexdigest(),
                "tool_schema_sha256": hashlib.sha256(
                    json.dumps(tools.definitions(), sort_keys=True).encode()
                ).hexdigest(),
                "model_settings": s.models.model_dump(),
                "backend": backend,
                "startup_seconds": startup,
                "warm_selection_accuracy": sum(r["pass"] for r in warm) / len(warm),
                "warm_p95_ttft_ms": percentile(
                    [r["ttft_ms"] for r in warm if r["ttft_ms"] is not None]
                ),
                "max_rss_bytes": max(r["rss_bytes"] for r in records),
                "platform": platform.platform(),
                "scope": "Text and tool-selection screening only. No actions executed. Not audible latency or release acceptance.",
            }
            destination.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2))
            print(json.dumps(summary), flush=True)
        finally:
            if llm:
                llm.close()
            if tools:
                tools.close()
            supervisor.stop_all()
            supervisor.client.close()
            supervisor.auth.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--case-set", choices=["core", "holdout"], default="core")
    parser.add_argument("--backend", choices=["llama", "mlx"], default="llama")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(args.model, args.backend, args.output, args.case_set)

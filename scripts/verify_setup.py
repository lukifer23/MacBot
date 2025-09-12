#!/usr/bin/env python3
"""
MacBot Verify Script

Checks local endpoints for the Orchestrator, Web Dashboard, Voice Assistant,
LLM server, and RAG server, and prints a concise status report.
"""
import os
import sys
import json
import time
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
from macbot import config as CFG


def check(name, url, method='GET', timeout=2, json_body=None):
    try:
        if method == 'GET':
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=json_body or {}, timeout=timeout)
        ok = 200 <= r.status_code < 300
        return ok, r.status_code, (r.text[:200] if r.text else '')
    except Exception as e:
        return False, None, str(e)


def main():
    wd_host, wd_port = CFG.get_web_dashboard_host_port()
    rag_host, rag_port = CFG.get_rag_host_port()
    va_host, va_port = CFG.get_voice_assistant_host_port()
    orc_host, orc_port = CFG.get_orchestrator_host_port()
    llm_models = CFG.get_llm_models_endpoint()

    tests = [
        ("Orchestrator /health", f"http://{orc_host}:{orc_port}/health"),
        ("Orchestrator /status", f"http://{orc_host}:{orc_port}/status"),
        ("Web Dashboard /health", f"http://{wd_host}:{wd_port}/health"),
        ("Voice Assistant /health", f"http://{va_host}:{va_port}/health"),
        ("RAG /health", f"http://{rag_host}:{rag_port}/health"),
        ("LLM /v1/models", llm_models),
    ]

    print("\nMacBot Verify Report")
    print("=" * 40)
    overall_ok = True
    for name, url in tests:
        ok, code, text = check(name, url)
        status = "OK" if ok else "FAIL"
        print(f"{name:24} {status:4} ({code}) -> {url}")
        if not ok:
            overall_ok = False
            if text:
                print(f"  Reason: {text}")

    if overall_ok:
        print("\nAll core endpoints are reachable. ✅")
        sys.exit(0)
    else:
        print("\nOne or more endpoints failed. See details above. ❌")
        sys.exit(1)


if __name__ == '__main__':
    main()


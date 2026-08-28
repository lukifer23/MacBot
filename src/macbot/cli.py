"""Explicit provisioning, lifecycle, migration and verification commands."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from importlib.resources import files
from pathlib import Path

import httpx
import psutil
import yaml

from .auth import AuthStore
from .config import Settings, atomic_write, load, prepare, save
from .provision import (
    build_audio,
    catalog,
    download,
    install_binaries,
    model_dir,
    verify,
    voice_model,
)

REVISIONS = {
    "llama.cpp": "c1d0e7a004015f23bc0233470b747b596f29b264",
    "whisper.cpp": "371b5a7561823ab2bb32142d2751e35e7534727b",
}


def build_inference(settings: Settings, source: Path | None = None):
    root = source or settings.data_dir / "sources"
    for name, revision in REVISIONS.items():
        repo = root / "models" / name
        if not repo.exists():
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/ggml-org/" + name,
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", revision], check=True
            )
            subprocess.run(["git", "-C", str(repo), "checkout", "--detach", revision], check=True)
        actual = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        if actual != revision:
            raise RuntimeError(f"{name}: expected {revision}; source checkout was not modified")
        subprocess.run(
            [
                "cmake",
                "-S",
                str(repo),
                "-B",
                str(repo / "build"),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=OFF",
                "-DGGML_METAL=ON",
            ],
            check=True,
        )
        targets = (
            ["llama-server", "llama-bench"]
            if name == "llama.cpp"
            else ["whisper-server", "whisper-cli"]
        )
        subprocess.run(
            ["cmake", "--build", str(repo / "build"), "--target", *targets, "-j", "4"], check=True
        )
    install_binaries(settings, root)
    native = files("macbot").joinpath("native")
    target = settings.data_dir / "build/whisper-worker"
    subprocess.run(
        [
            "cmake",
            "-S",
            str(native),
            "-B",
            str(target),
            "-DWHISPER_SOURCE=" + str(root / "models/whisper.cpp"),
        ],
        check=True,
    )
    subprocess.run(
        ["cmake", "--build", str(target), "--target", "macbot-whisper", "-j", "4"], check=True
    )
    shutil.copy2(target / "macbot-whisper", settings.data_dir / "bin/macbot-whisper")
    atomic_write(settings.data_dir / "bin/versions.json", json.dumps(REVISIONS).encode())


def doctor(settings: Settings) -> dict:
    checks = {}
    needed = [
        settings.models.llm,
        "parakeet" if settings.models.stt == "parakeet" else "whisper-base",
        voice_model(settings.models.tts_voice),
        "minilm",
        "silero",
    ]
    for name in needed:
        try:
            model_dir(settings, name)
            checks[name] = {"present": True, "integrity": "run models verify for full hashes"}
        except (ValueError, FileNotFoundError) as exc:
            checks[name] = {"present": False, "error": str(exc)}
    for binary in ["macbot-audio"] + (
        ["llama-server"] if settings.models.llm_backend == "llama" else []
    ):
        checks[binary] = {"present": (settings.data_dir / "bin" / binary).is_file()}
    checks["ffmpeg"] = {"present": bool(shutil.which("ffmpeg"))}
    return {
        "ready_to_start": all(c["present"] for c in checks.values()),
        "checks": checks,
        "device_acceptance": "not_verified",
        "model_selection": "candidate_not_benchmarked",
    }


def migrate_config(settings: Settings, source: Path):
    old = yaml.safe_load(source.read_text())
    if not isinstance(old, dict):
        raise ValueError("Legacy config must be a mapping")
    backup = settings.data_dir / "backups" / ("config-" + str(time.time_ns()) + ".yaml")
    atomic_write(backup, source.read_bytes())
    if old.get("version") == 2:
        raise ValueError("Configuration is already version 2")
    # Preserve unsupported fields in the backup and report them; never silently discard model paths.
    models = old.get("models", {})
    llm = models.get("llm", {})
    for key in ["context_length", "max_tokens", "temperature"]:
        if key in llm:
            setattr(settings.models, key, llm[key])
    tools = old.get("tools", {})
    settings.tools.allowed_apps = tools.get("app_launcher", {}).get(
        "allowed_apps", settings.tools.allowed_apps
    )
    mapping = {
        "system_monitor": "system_info",
        "rag_search": "rag_search",
        "app_launcher": "open_app",
        "screenshot": "screenshot",
        "web_search": "web_search",
        "weather": "weather",
    }
    if "enabled" in tools:
        settings.tools.enabled = [mapping[n] for n in tools["enabled"] if n in mapping]
    save(settings)
    return {
        "config": str(settings.config_path),
        "backup": str(backup),
        "review_required": "Legacy model files are preserved at their original paths. Provision registered models before starting; no model file was deleted.",
    }


def main():
    parser = argparse.ArgumentParser(description="MacBot: local voice with explicit permissions")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    sub.add_parser("doctor")
    sub.add_parser("build-audio")
    build = sub.add_parser("build-inference")
    build.add_argument("--source", type=Path)
    models = sub.add_parser("models")
    models.add_argument("action", choices=["list", "download", "verify"])
    models.add_argument("names", nargs="*")
    start = sub.add_parser("start")
    start.add_argument("--background", action="store_true")
    for name in ["stop", "status", "open", "orchestrator", "dashboard", "rag", "voice", "verify"]:
        sub.add_parser(name)
    migration = sub.add_parser("migrate-config")
    migration.add_argument("--source", type=Path, required=True)
    migration = sub.add_parser("migrate-rag")
    migration.add_argument("--source", type=Path, required=True)
    restore_parser = sub.add_parser("restore-rag")
    restore_parser.add_argument("--backup", type=Path, required=True)
    sub.add_parser("rebuild-index")
    args = parser.parse_args()
    try:
        settings = load(args.config)
        if args.config:
            os.environ["MACBOT_CONFIG"] = str(Path(args.config).resolve())
        os.environ["MACBOT_DATA_DIR"] = str(settings.data_dir)
        if args.command == "setup":
            prepare(settings)
            print(settings.config_path)
            return
        if args.command == "doctor":
            print(json.dumps(doctor(settings), indent=2))
            return
        if args.command == "build-audio":
            prepare(settings)
            print(build_audio(settings))
            return
        if args.command == "build-inference":
            prepare(settings)
            build_inference(settings, args.source)
            return
        if args.command == "models":
            prepare(settings)
            if args.action == "list":
                print(json.dumps(catalog(), indent=2))
                return
            if not args.names:
                raise ValueError("Specify model names from macbot models list")
            for name in args.names:
                print(
                    json.dumps((download if args.action == "download" else verify)(settings, name))
                )
            return
        if args.command == "migrate-config":
            prepare(settings)
            print(json.dumps(migrate_config(settings, args.source), indent=2))
            return
        if args.command in {"migrate-rag", "restore-rag", "rebuild-index"}:
            prepare(settings)
            # Offline maintenance requires the managed RAG listener to be stopped.
            import socket

            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", settings.services.rag.port)) == 0:
                    raise RuntimeError("Stop MacBot before RAG maintenance")
            if args.command == "restore-rag":
                from .retrieval import restore

                previous = restore(settings, args.backup)
                print(
                    json.dumps(
                        {
                            "restored": str(settings.data_dir / "rag"),
                            "previous_store": str(previous),
                        }
                    )
                )
                return
            from .retrieval import DocumentStore

            store = DocumentStore(settings, maintenance=args.command == "rebuild-index")
            try:
                if args.command == "migrate-rag":
                    print(json.dumps(store.migrate(args.source), indent=2))
                else:
                    store.rebuild()
                    print(json.dumps(store.stats()))
            finally:
                store.close()
            return
        if args.command == "start":
            prepare(settings)
            # Store the effective validated config for consistent child-process settings.
            save(settings)
            if args.background:
                log = (settings.data_dir / "logs/supervisor.log").open("ab")
                proc = subprocess.Popen(
                    [sys.executable, "-m", "macbot.cli", "orchestrator"],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                log.close()
                print(json.dumps({"state": "starting", "pid": proc.pid}))
                return
            from .orchestrator import main as run

            run()
            return
        if args.command in {"orchestrator", "dashboard", "rag", "voice"}:
            import importlib

            names = {"dashboard": "web_dashboard", "rag": "rag_server", "voice": "voice_assistant"}
            importlib.import_module("macbot." + names.get(args.command, args.command)).main()
            return
        auth = AuthStore(settings.data_dir)
        try:
            if args.command == "open":
                token = auth.issue_login()
                webbrowser.open(settings.services.dashboard.url + "/#token=" + token)
                print("Opened a single-use local login link (expires in 60 seconds).")
                return
            path = "/shutdown" if args.command == "stop" else "/status"
            with httpx.Client(timeout=5, trust_env=False) as client:
                owned = []
                if args.command == "stop":
                    before = client.get(
                        settings.services.orchestrator.url + "/status",
                        headers=auth.headers("orchestrator"),
                    ).json()
                    pids = [
                        before.get("pid"),
                        *[s.get("pid") for s in before.get("services", {}).values()],
                    ]
                    for pid in pids:
                        if pid:
                            try:
                                owned.append(psutil.Process(pid))
                            except psutil.NoSuchProcess:
                                pass
                response = client.request(
                    "POST" if args.command == "stop" else "GET",
                    settings.services.orchestrator.url + path,
                    headers=auth.headers("orchestrator"),
                )
                response.raise_for_status()
                if args.command == "stop":
                    _, alive = psutil.wait_procs(owned, timeout=35)
                    if alive:
                        raise RuntimeError(
                            "Owned processes are still stopping; inspect logs before restarting"
                        )
                    print(json.dumps({"state": "stopped"}))
                else:
                    print(json.dumps(response.json(), indent=2))
            if args.command == "verify":
                print(
                    "Service readiness only. Run the integration, benchmark and device release gates separately."
                )
        finally:
            auth.close()
    except Exception as exc:
        print(f"MacBot: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

"""Explicit, checksum-verified model provisioning; never called by inference."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, atomic_write

KOKORO_VOICES = {"kokoro-heart": "af_heart", "kokoro-michael": "am_michael"}


def voice_model(name: str) -> str:
    return "kokoro" if name in KOKORO_VOICES else name


def catalog() -> dict[str, Any]:
    return json.loads(files("macbot").joinpath("defaults/models.json").read_text())


def voices() -> list[str]:
    return [
        name
        for name, item in catalog().items()
        if any(f["name"].endswith(".onnx.json") for f in item["files"])
    ] + list(KOKORO_VOICES)


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def model_dir(settings: Settings, name: str) -> Path:
    entries = catalog()
    if name not in entries:
        raise ValueError(f"Unregistered model: {name}")
    path = settings.data_dir / "models" / name
    for entry in entries[name]["files"]:
        target = path / entry["name"]
        if not target.is_file() or target.stat().st_size != entry["size"]:
            raise FileNotFoundError(
                f"Model {name} not provisioned; run macbot models download {name}"
            )
    return path


def model_file(settings: Settings, name: str, suffix: str) -> Path:
    root = model_dir(settings, name)
    matches = [e["name"] for e in catalog()[name]["files"] if e["name"].endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one {suffix} artifact for {name}, got {len(matches)}")
    return root / matches[0]


def download(settings: Settings, name: str) -> dict[str, Any]:
    item = catalog()[name]
    root = settings.data_dir / "models" / name
    with httpx.Client(
        follow_redirects=True, timeout=httpx.Timeout(60, connect=10), trust_env=False
    ) as client:
        for entry in item["files"]:
            target = root / entry["name"]
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if target.is_file() and sha256(target) == entry["sha256"]:
                continue
            partial = target.with_suffix(target.suffix + ".part")
            try:
                h = hashlib.sha256()
                with client.stream("GET", entry["url"]) as response:
                    if not response.is_success:
                        # Redirects may contain signed CDN credentials; never include
                        # the response URL in user-visible exceptions or logs.
                        raise RuntimeError(
                            f"Download failed for {name}/{entry['name']}: HTTP {response.status_code}. Retry provisioning later."
                        )
                    with partial.open("wb") as f:
                        os.chmod(partial, 0o600)
                        size = 0
                        for block in response.iter_bytes(1024 * 1024):
                            size += len(block)
                            if size > entry["size"]:
                                raise ValueError(
                                    f"Unexpected artifact size for {name}/{entry['name']}"
                                )
                            h.update(block)
                            f.write(block)
                        f.flush()
                        os.fsync(f.fileno())
                if h.hexdigest() != entry["sha256"]:
                    raise ValueError(f"Checksum mismatch for {name}/{entry['name']}")
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
    atomic_write(root / "receipt.json", json.dumps(item, indent=2).encode())
    return {"model": name, "revision": item["revision"], "verified": True}


def verify(settings: Settings, name: str) -> dict[str, Any]:
    item = catalog()[name]
    root = model_dir(settings, name)
    bad = [e["name"] for e in item["files"] if sha256(root / e["name"]) != e["sha256"]]
    if bad:
        raise ValueError(f"Model checksum mismatch: {name}: {bad}")
    return {"model": name, "revision": item["revision"], "verified": True}


def native_binary(settings: Settings) -> Path:
    path = settings.data_dir / "bin" / "macbot-audio"
    if not path.is_file():
        raise FileNotFoundError("Native audio helper missing; run macbot build-audio")
    return path


def build_audio(settings: Settings) -> Path:
    out = settings.data_dir / "bin" / "macbot-audio"
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = files("macbot").joinpath("native/AudioBridge.swift")
    plist = files("macbot").joinpath("native/Info.plist")
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-O",
            "-swift-version",
            "5",
            str(source),
            "-framework",
            "AVFoundation",
            "-framework",
            "AVFAudio",
            "-Xlinker",
            "-sectcreate",
            "-Xlinker",
            "__TEXT",
            "-Xlinker",
            "__info_plist",
            "-Xlinker",
            str(plist),
            "-o",
            str(out),
        ],
        check=True,
        timeout=120,
    )
    subprocess.run(
        ["codesign", "--force", "--sign", "-", "--identifier", "local.macbot.audio", str(out)],
        check=True,
        timeout=30,
    )
    return out


def install_binaries(settings: Settings, repo: Path) -> None:
    out = settings.data_dir / "bin"
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    for component, names in (
        ("llama.cpp", ["llama-server", "llama-bench"]),
        ("whisper.cpp", ["whisper-server", "whisper-cli"]),
    ):
        source = repo / "models" / component / "build" / "bin"
        for name in names:
            if not (source / name).is_file():
                raise FileNotFoundError(f"Build {component} before installing binaries")
            shutil.copy2(source / name, out / name)

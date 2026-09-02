"""Explicit, checksum-verified model provisioning; never called by inference."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, atomic_write, release_model_manifest

QWEN_TTS_VOICES = {
    "qwen-aiden-0.6b": ("qwen3-tts-0.6b", "Aiden"),
    "qwen-ryan-0.6b": ("qwen3-tts-0.6b", "Ryan"),
    "qwen-aiden-1.7b": ("qwen3-tts-1.7b", "Aiden"),
    "qwen-ryan-1.7b": ("qwen3-tts-1.7b", "Ryan"),
}

_ATTESTATION_LOCK = threading.Lock()
_ATTESTED_FILES: dict[tuple[str, int, int, int, str], bool] = {}


def voice_model(name: str) -> str:
    if name in QWEN_TTS_VOICES:
        return QWEN_TTS_VOICES[name][0]
    return name


def catalog() -> dict[str, Any]:
    entries = json.loads(files("macbot").joinpath("defaults/models.json").read_text())
    if not isinstance(entries, dict):
        raise ValueError("Model catalog must be a mapping")
    selected = release_model_manifest()
    missing = sorted(
        entry["artifact"] for entry in selected.values() if entry["artifact"] not in entries
    )
    if missing:
        raise ValueError(
            "Release model artifacts are absent from the catalog: " + ", ".join(missing)
        )
    return entries


def release_artifacts() -> dict[str, str]:
    """Return the one production artifact selected for each typed model role."""
    return {role: str(entry["artifact"]) for role, entry in release_model_manifest().items()}


def model_roles(name: str) -> tuple[str, ...]:
    """Classify an artifact without treating benchmark candidates as production."""
    entries = catalog()
    if name not in entries:
        raise ValueError(f"Unregistered model: {name}")
    return tuple(role for role, artifact in release_artifacts().items() if artifact == name)


def installed_model_inventory(settings: Settings) -> dict[str, Any]:
    """Report selected and extra artifacts; never delete or silently relocate either."""
    root = settings.data_dir / "models"
    installed = (
        sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
    )
    selected = release_artifacts()
    selected_names = set(selected.values())
    return {
        "selected": selected,
        "installed": installed,
        "extra": [name for name in installed if name not in selected_names],
    }


def voices() -> list[str]:
    selected = release_model_manifest()["tts"]
    voice = selected.get("voice")
    if not isinstance(voice, str) or voice_model(voice) != selected["artifact"]:
        raise ValueError("Release TTS voice does not match its selected artifact")
    return [voice]


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
        stat = target.stat()
        key = (str(target), stat.st_ino, stat.st_size, stat.st_mtime_ns, entry["sha256"])
        with _ATTESTATION_LOCK:
            attested = key in _ATTESTED_FILES
        if not attested:
            if sha256(target) != entry["sha256"]:
                raise ValueError(f"Model checksum mismatch: {name}: {entry['name']}")
            with _ATTESTATION_LOCK:
                _ATTESTED_FILES[key] = True
    return path


def model_file(settings: Settings, name: str, suffix: str) -> Path:
    root = model_dir(settings, name)
    matches = [e["name"] for e in catalog()[name]["files"] if e["name"].endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one {suffix} artifact for {name}, got {len(matches)}")
    return root / matches[0]


def _download_files(
    item: dict[str, Any], root: Path, key: str = "files", *, model_name: str
) -> None:
    with httpx.Client(
        follow_redirects=True, timeout=httpx.Timeout(60, connect=10), trust_env=False
    ) as client:
        for entry in item[key]:
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
                            f"Download failed for {model_name}/{entry['name']}: HTTP {response.status_code}. Retry provisioning later."
                        )
                    with partial.open("wb") as f:
                        os.chmod(partial, 0o600)
                        size = 0
                        for block in response.iter_bytes(1024 * 1024):
                            size += len(block)
                            if size > entry["size"]:
                                raise ValueError(
                                    f"Unexpected artifact size for {model_name}/{entry['name']}"
                                )
                            h.update(block)
                            f.write(block)
                        f.flush()
                        os.fsync(f.fileno())
                if h.hexdigest() != entry["sha256"]:
                    raise ValueError(f"Checksum mismatch for {model_name}/{entry['name']}")
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)


def _verify_files(root: Path, entries: list[dict[str, Any]]) -> list[str]:
    return [
        entry["name"]
        for entry in entries
        if not (root / entry["name"]).is_file()
        or (root / entry["name"]).stat().st_size != entry["size"]
        or sha256(root / entry["name"]) != entry["sha256"]
    ]


def _convert_qwen_tts(settings: Settings, name: str, item: dict[str, Any]) -> None:
    models = settings.data_dir / "models"
    models.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=models) as temporary:
        stage = Path(temporary)
        source, converted = stage / "source", stage / "converted"
        _download_files(item, source, "source_files", model_name=name)
        conversion = item["conversion"]
        environment = os.environ.copy()
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "mlx_audio.convert",
                "--hf-path",
                str(source),
                "--mlx-path",
                str(converted),
                "--quantize",
                "--q-bits",
                str(conversion["q_bits"]),
                "--q-group-size",
                str(conversion["q_group_size"]),
                "--model-domain",
                conversion["model_domain"],
            ],
            env=environment,
            check=True,
            timeout=1800,
        )
        bad = _verify_files(converted, item["files"])
        if bad:
            raise ValueError(f"Converted model verification failed: {name}: {bad}")
        atomic_write(converted / "receipt.json", json.dumps(item, indent=2).encode())
        target = models / name
        if target.exists():
            backup = settings.data_dir / "backups" / f"invalid-model-{name}-{time.time_ns()}"
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.rename(backup)
        os.replace(converted, target)


def _convert_llama_gguf(settings: Settings, name: str, item: dict[str, Any]) -> None:
    """Build a registered GGUF from checksum-verified official source weights."""
    conversion = item["conversion"]
    source_repo = settings.data_dir / "sources/models/llama.cpp"
    converter = source_repo / "convert_hf_to_gguf.py"
    converter_python = source_repo / ".venv/bin/python"
    quantizer = settings.data_dir / "bin/llama-quantize"
    if not converter.is_file() or not converter_python.is_file() or not quantizer.is_file():
        raise FileNotFoundError(
            "Pinned llama.cpp converter environment/quantizer missing; run macbot build-inference first"
        )
    actual_revision = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    if actual_revision != conversion["converter_revision"]:
        raise ValueError("llama.cpp converter revision does not match the model receipt")
    if sha256(converter) != conversion["converter_sha256"]:
        raise ValueError("llama.cpp converter checksum does not match the model receipt")

    models = settings.data_dir / "models"
    models.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=models) as temporary:
        stage = Path(temporary)
        source, converted = stage / "source", stage / "converted"
        _download_files(item, source, "source_files", model_name=name)
        converted.mkdir(mode=0o700)
        f16 = stage / "intermediate-f16.gguf"
        output = converted / item["files"][0]["name"]
        environment = os.environ.copy()
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        try:
            subprocess.run(
                [
                    str(converter_python),
                    str(converter),
                    str(source),
                    "--outfile",
                    str(f16),
                    "--outtype",
                    "f16",
                ],
                cwd=source_repo,
                env=environment,
                check=True,
                timeout=1800,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Official model conversion failed in the pinned llama.cpp environment"
            ) from exc
        if sha256(f16) != conversion["intermediate_sha256"]:
            raise ValueError("Converted F16 intermediate checksum does not match the receipt")
        subprocess.run(
            [str(quantizer), str(f16), str(output), conversion["quantization"]],
            check=True,
            timeout=1800,
        )
        bad = _verify_files(converted, item["files"])
        if bad:
            raise ValueError(f"Converted model verification failed: {name}: {bad}")
        atomic_write(converted / "receipt.json", json.dumps(item, indent=2).encode())
        target = models / name
        if target.exists():
            backup = settings.data_dir / "backups" / f"invalid-model-{name}-{time.time_ns()}"
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.rename(backup)
        os.replace(converted, target)


def download(settings: Settings, name: str) -> dict[str, Any]:
    item = catalog()[name]
    root = settings.data_dir / "models" / name
    if "conversion" in item:
        try:
            if not _verify_files(root, item["files"]):
                return {"model": name, "revision": item["revision"], "verified": True}
        except FileNotFoundError:
            pass
        conversion_type = item["conversion"]["type"]
        if conversion_type == "mlx_audio_q4":
            _convert_qwen_tts(settings, name, item)
        elif conversion_type == "llama_cpp_q4_k_m":
            _convert_llama_gguf(settings, name, item)
        else:
            raise ValueError(f"Unsupported registered conversion: {conversion_type}")
        return {"model": name, "revision": item["revision"], "verified": True}
    _download_files(item, root, model_name=name)
    atomic_write(root / "receipt.json", json.dumps(item, indent=2).encode())
    return {"model": name, "revision": item["revision"], "verified": True}


def verify(settings: Settings, name: str) -> dict[str, Any]:
    item = catalog()[name]
    model_dir(settings, name)
    return {"model": name, "revision": item["revision"], "verified": True}


def install_binaries(settings: Settings, repo: Path) -> None:
    out = settings.data_dir / "bin"
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    component = "llama.cpp"
    source = repo / "models" / component / "build" / "bin"
    for name in ["llama-server", "llama-bench", "llama-quantize"]:
        if not (source / name).is_file():
            raise FileNotFoundError(f"Build {component} before installing binaries")
        shutil.copy2(source / name, out / name)

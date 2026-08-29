"""Explicit, checksum-verified model provisioning; never called by inference."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, atomic_write

KOKORO_VOICES = {"kokoro-heart": "af_heart", "kokoro-michael": "am_michael"}
QWEN_TTS_VOICES = {
    "qwen-aiden-0.6b": ("qwen3-tts-0.6b", "Aiden"),
    "qwen-ryan-0.6b": ("qwen3-tts-0.6b", "Ryan"),
    "qwen-aiden-1.7b": ("qwen3-tts-1.7b", "Aiden"),
    "qwen-ryan-1.7b": ("qwen3-tts-1.7b", "Ryan"),
}


def voice_model(name: str) -> str:
    if name in QWEN_TTS_VOICES:
        return QWEN_TTS_VOICES[name][0]
    return "kokoro" if name in KOKORO_VOICES else name


def catalog() -> dict[str, Any]:
    return json.loads(files("macbot").joinpath("defaults/models.json").read_text())


def voices() -> list[str]:
    return (
        [
            name
            for name, item in catalog().items()
            if any(f["name"].endswith(".onnx.json") for f in item["files"])
        ]
        + list(KOKORO_VOICES)
        + list(QWEN_TTS_VOICES)
    )


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
    root = model_dir(settings, name)
    bad = _verify_files(root, item["files"])
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
        ("llama.cpp", ["llama-server", "llama-bench", "llama-quantize"]),
        ("whisper.cpp", ["whisper-server", "whisper-cli"]),
    ):
        source = repo / "models" / component / "build" / "bin"
        for name in names:
            if not (source / name).is_file():
                raise FileNotFoundError(f"Build {component} before installing binaries")
            shutil.copy2(source / name, out / name)

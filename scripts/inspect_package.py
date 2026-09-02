"""Fail distribution inspection when private state or required assets are misplaced."""

import tarfile
import tomllib
import zipfile
from pathlib import Path

required = {
    "macbot/templates/dashboard.html",
    "macbot/static/dashboard.js",
    "macbot/static/event-feed.js",
    "macbot/defaults/config.yaml",
    "macbot/defaults/models.json",
    "macbot/defaults/lab_models.json",
    "macbot/defaults/release_models.json",
    "macbot/defaults/task_protocol_v3.json",
}
version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
artifacts = list(Path("dist").glob(f"macbot-{version}-*.whl")) + list(
    Path("dist").glob(f"macbot-{version}.tar.gz")
)
assert artifacts, "No distribution artifacts to inspect"
for path in artifacts:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        assert required <= set(names), required - set(names)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            names = archive.getnames()
        # Anchor the source allowlist at the archive root. A bare "docs" or
        # "tests" Hatch pattern also matches vendor submodule directories.
        allowed = {
            "src",
            "tests",
            "scripts",
            "docs",
            "config",
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "Makefile",
            "LICENSE",
            "PKG-INFO",
            ".gitignore",
        }
        assert all(name.split("/")[1] in allowed for name in names), (
            "Unexpected source archive root"
        )
    else:
        continue
    bad = [
        name
        for name in names
        if any(
            part in name
            for part in [
                "rag_data/",
                "recordings/",
                "credentials",
                "service-keys",
                "logs/",
                ".sqlite",
                ".gguf",
                ".onnx",
                ".safetensors",
                ".wav",
                ".mp3",
                ".flac",
                "/.env",
            ]
        )
    ]
    obsolete = [
        name
        for name in names
        if "/native/AudioBridge.swift" in name or "/native/Info.plist" in name
    ]
    assert not bad, (path, bad)
    assert not obsolete, (path, obsolete)
    print(path.name, "contents verified", len(names), "files")

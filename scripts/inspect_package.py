"""Fail distribution inspection when private state or required assets are misplaced."""

import tarfile
import zipfile
from pathlib import Path

required = {
    "macbot/templates/dashboard.html",
    "macbot/static/dashboard.js",
    "macbot/static/event-feed.js",
    "macbot/static/socket.io.min.js",
    "macbot/defaults/config.yaml",
    "macbot/defaults/models.json",
    "macbot/native/AudioBridge.swift",
    "macbot/native/Info.plist",
}
artifacts = list(Path("dist").glob("*.whl")) + list(Path("dist").glob("*.tar.gz"))
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
    assert not bad, (path, bad)
    print(path.name, "contents verified", len(names), "files")

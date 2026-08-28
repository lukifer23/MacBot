# Development and installation

Use Python 3.12 and uv. `pyproject.toml` is the only dependency definition; commit `uv.lock` after deliberate dependency changes. `requirements*.txt` are compatibility redirects, not separate pins. No LiveKit initialization, PyPDF2 or heavyweight unused embedding load belongs in the supported runtime.

```sh
uv sync --frozen --all-extras --group dev
uv run --frozen macbot setup
uv run --frozen macbot build-inference --source "$PWD"
uv run --frozen macbot build-audio
make models
make verify
```

`build-inference` verifies the source revision before building. Without `--source`, it provisions pinned native sources under the data directory, allowing installed-wheel use outside a checkout. `build-audio` compiles the packaged Swift source, embeds microphone-purpose metadata and applies an ad-hoc local signature. Xcode Command Line Tools, CMake and FFmpeg are system prerequisites; setup does not install Homebrew or rewrite system settings.

Model downloads are explicit. Each registry entry includes upstream revision, filenames, size, SHA-256 and licensing provenance. `macbot models verify NAME...` rehashes installed artifacts. Runtime checks presence and size and fails when required artifacts are missing; inference never calls provisioning. Run full hash verification after transfers or before release.

```sh
uv build
uv run --frozen python -m zipfile -l dist/macbot-2.0.0-py3-none-any.whl
```

A wheel must contain templates, static assets, model catalog, default config and native source/build metadata. It must not contain user documents, model weights, recordings, databases, credentials or logs. Test installation into a clean environment and execute from a different directory, with offline mode after provisioning.

All changes stay on main for this modernization. Commit cohesive verified changes, reconcile upstream without force or data loss, and push only after every [release gate](VERIFICATION.md) has passed. Hosted CI evidence and local/device evidence are distinct.

# Development and installation

Use Python 3.12 and uv. `pyproject.toml` is the only dependency definition; commit `uv.lock` after deliberate dependency changes. `requirements*.txt` are compatibility redirects, not separate pins. No LiveKit initialization, PyPDF2 or heavyweight unused embedding load belongs in the supported runtime.

```sh
uv sync --frozen --all-extras --group dev
uv run --frozen python -c 'import macbot; print(macbot.__file__)'
uv run --frozen macbot doctor
uv run --frozen macbot setup
uv run --frozen macbot build-inference --source "$PWD"
make models
make verify
```

`build-inference` verifies the source revision before building. Without `--source`,
it provisions pinned native sources under the data directory, allowing
installed-wheel use outside a checkout. Swift owns released capture and
playback; there is no Python audio-helper build. Xcode Command Line Tools,
CMake, and FFmpeg are system prerequisites; setup does not install Homebrew or
rewrite system settings.

Model downloads are explicit. Each registry entry includes upstream revision, filenames, size, SHA-256 and licensing provenance. `macbot models verify NAME...` rehashes installed artifacts. Runtime checks presence and size and fails when required artifacts are missing; inference never calls provisioning. Run full hash verification after transfers or before release.

```sh
uv build
uv run --frozen python scripts/inspect_package.py
```

A wheel must contain templates, static assets, the checksum artifact catalog,
typed lab and production manifests, default config, and protocol-v3 schema. It must not contain user
documents, model weights, recordings, databases, credentials, logs, or the old
audio helper. Test installation into a clean environment and execute from a
different directory, with offline mode after provisioning.

`./scripts/build_native_app.sh --install` stages a clean runtime and app as one
generation, verifies both, writes `release-manifest.json`, and atomically swaps
the single `current` pointer used by stable app/runtime links. If the installed
runtime is active, the installer stops its owned processes, acquires the
host-wide inference lease, activates the pair, and restores runtime readiness.
Failed validation restores the exact prior pointers. Do not activate an app
from one generation with a runtime from another.

The quality gates are intentionally separate:

```sh
# software
make verify

# real macOS process/transport integration
make native-integration

# selected model and Swift tests
uv run --frozen pytest -m 'models and not device'
swift test --package-path native/MacBotApp -c release

# package artifact
uv build
uv run --frozen python scripts/inspect_package.py
./scripts/build_native_app.sh
```

Live native integration, device audio, soak, and installed release-artifact
acceptance are separate runs documented in [VERIFICATION.md](VERIFICATION.md).

All changes stay on main for this modernization. Commit cohesive verified
checkpoints, reconcile upstream without force or data loss, and label them
truthfully as checkpoints until every [release gate](VERIFICATION.md) has passed.
Hosted CI evidence and local/device evidence are distinct.

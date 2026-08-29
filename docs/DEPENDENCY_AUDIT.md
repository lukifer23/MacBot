# Dependency audit — 2026-08-29

The locked Apple Silicon runtime graph was installed locally and checked with
`pip-audit --local`. Four advisories were initially reported for
`cryptography 46.0.7`; MacBot was upgraded to `cryptography 50.0.1`, the lockfile
was regenerated, encrypted-history/authentication tests passed, and the repeated
audit reported **no known third-party vulnerabilities**. The local `macbot`
project itself is not published on PyPI and is therefore listed as unauditable.

Chroma and its former transitive graph are no longer runtime dependencies.
Legacy Chroma directories are treated only as preserved migration input; no
Chroma server or client is loaded by the current application.

The source secret scan found public upstream/model hashes and deliberate
negative-test fixtures. Generated build and tool-cache paths were excluded from
source review. No live credential was found. Brave credentials and history keys
remain in macOS Keychain and are never accepted through command-line arguments,
environment variables, files, URLs, or logs.

This is point-in-time evidence. Re-run the audit after every lockfile change and
before a release claim; do not suppress a future advisory with a blanket ignore.

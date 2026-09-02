# Migration and rollback

Stop MacBot before maintenance. Preserve the old checkout, model files, configuration and RAG directory until the new release passes acceptance. No automatic legacy-data migration or sample insertion occurs.

```sh
uv run --frozen macbot stop
uv run --frozen macbot migrate-config --source /absolute/path/old-config.yaml
uv run --frozen macbot migrate-rag --source /absolute/path/old-rag-data
```

Configuration migration keeps a private copy of the original, maps supported
settings, and reports model-path review requirements. Existing model files are
not removed. Version 2 configuration remains the settings schema; native
protocol versioning is independent. Release tools must resolve to exactly
`rag_search`, `web_search`, and `web_fetch`. Stale browser fallback, application
allowlist, Whisper, helper-audio, and candidate-voice keys are rejected rather
than silently retained. Provision the production model manifest explicitly.

RAG migration first copies the source. Legacy JSON records and authoritative
SQLite records are reconciled by document ID. Conflicting content or metadata
stops migration; resolve the discrepancy in a separate source copy and retry.

The exact vector index is derived data. If the authoritative SQLite database
references an index revision whose files are missing, startup first creates an
owner-only `incomplete-rag-*` backup and builds a new verified revision from the
preserved documents. Present-but-corrupt indexes and embedding-signature changes
still fail closed and require explicit inspection or `rebuild-index`; they are
not silently replaced.
The original source and backup remain unchanged. Source IDs and content are
retained, including duplicate legacy IDs with identical content. Repeat ordinary
imports deduplicate. A Chroma-only store without authoritative source content
cannot be migrated safely; export its source records with the old release first.

SQLite is authoritative. The replacement exact index uses explicit local MiniLM
ONNX embeddings, token-bounded chunks and source offsets in a memory-mapped
vector file. A new version is built and checked before the SQLite active-index
pointer commits. Previous index versions are retained. Do not manually delete
them before rollback acceptance.

```sh
uv run --frozen macbot rebuild-index
uv run --frozen macbot restore-rag --backup /absolute/path/to/reported/rollback-backup
```

Restore is offline maintenance and preserves the current directory under
`backups` before replacement. Keep the printed source and rollback backup paths.
To roll back code, use the preserved old checkout and its backed-up
configuration/data; do not point the old application at the new exact-index
directory.

## Paired app/runtime generations

`./scripts/build_native_app.sh --install` stages an app and runtime under one
release-generation directory, verifies both, writes `release-manifest.json`,
then quiesces the owned runtime and atomically swaps the single `current`
pointer while holding the host-wide inference lease. The pointer is shared by
the stable app and runtime links. The previous pointer is retained as
`rollback`; failed post-activation validation restores the exact earlier
pointers. Never copy only the app or
only the runtime over an installed generation. Before activation, preserve any
older non-symlink app as the timestamped `MacBot.previous-*.app` artifact.

Rollback selects the previous verified generation's app and runtime together.
Confirm its release manifest, source revision, protocol version, executable
hash, runtime hash, and selected model hashes before launching it. The installer
automatically restores the prior pair when post-activation validation fails;
there is not yet a public manual rollback command. Manual recovery must switch
the paired pointers under the same activation lock and host-wide inference
lease, then prove offline startup and state reconciliation. Merely changing a
pointer is not rollback acceptance.

Only `current` and `rollback` are authoritative. The installer does not yet
prune older generation directories automatically, so generation retention is
an open release gate. Until pruning is implemented, identify both symlink
targets first and remove no generation manually during an upgrade or rollback.

Migration tests using temporary real stores do not prove compatibility with
every historical Chroma format. A copy of the user's actual legacy source store
must pass reconciliation, retrieval and rollback before release.

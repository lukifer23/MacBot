# Migration and rollback

Stop MacBot before maintenance. Preserve the old checkout, model files, configuration and RAG directory until the new release passes acceptance. No automatic legacy-data migration or sample insertion occurs.

```sh
uv run --frozen macbot stop
uv run --frozen macbot migrate-config --source /absolute/path/old-config.yaml
uv run --frozen macbot migrate-rag --source /absolute/path/old-rag-data
```

Configuration migration keeps a private copy of the original, maps supported settings, and reports model-path review requirements. Existing model files are not removed. Version 2 accepts registered models; provision/select the corresponding model explicitly. Inspect the migrated settings, especially disabled tools and allowed applications, before starting.

RAG migration first copies the source. Legacy JSON records and authoritative
SQLite records are reconciled by document ID. Conflicting content or metadata
stops migration; resolve the discrepancy in a separate source copy and retry.
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

Migration tests using temporary real stores do not prove compatibility with
every historical Chroma format. A copy of the user's actual legacy source store
must pass reconciliation, retrieval and rollback before release.

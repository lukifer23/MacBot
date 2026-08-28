"""Transactional source documents and versioned, rebuildable Chroma indexes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from .config import Settings
from .provision import catalog, model_dir


class Embedder:
    def __init__(self, settings: Settings):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        root = model_dir(settings, "minilm")
        self.tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.encoder = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.encoder.enable_truncation(max_length=256)
        self.encoder.enable_padding(pad_id=0, pad_token="[PAD]")
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(root / "onnx/model_qint8_arm64.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.signature = catalog()["minilm"]["revision"] + ":int8-arm64:mean:l2:224:32"
        self.lock = threading.Lock()

    def chunks(self, content: str) -> list[dict]:
        tokens = self.tokenizer.encode(content, add_special_tokens=False)
        chunks = []
        for index in range(0, len(tokens.ids), 192):
            selected = tokens.offsets[index : index + 224]
            start, end = selected[0][0], selected[-1][1]
            chunks.append({"content": content[start:end], "start": start, "end": end})
            if index + 224 >= len(tokens.ids):
                break
        return chunks

    def encode(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            encoded = self.encoder.encode_batch(texts)
            inputs = {
                "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
            }
            output = self.session.run(
                None, {i.name: inputs[i.name] for i in self.session.get_inputs()}
            )[0]
            if output.ndim == 3:
                mask = inputs["attention_mask"][..., None]
                output = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            output = output.astype(np.float32)
            return output / np.maximum(np.linalg.norm(output, axis=1, keepdims=True), 1e-12)


class DocumentStore:
    def __init__(self, settings: Settings, *, maintenance: bool = False):
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.settings = settings
        self.root = settings.data_dir / "rag"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner = (settings.data_dir / "rag.lock").open("a")
        os.fchmod(self.owner.fileno(), 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.owner.close()
            raise RuntimeError("Another process owns this document store") from None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "documents.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, content TEXT NOT NULL, title TEXT NOT NULL, type TEXT NOT NULL, metadata TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS embeddings(key TEXT PRIMARY KEY, vector BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(name TEXT PRIMARY KEY, signature TEXT NOT NULL, created REAL NOT NULL, document_ids TEXT NOT NULL);
        """)
        self.db.commit()
        (self.root / "documents.sqlite3").chmod(0o600)
        self.embedder = Embedder(settings)
        self.client = chromadb.PersistentClient(
            path=str(self.root / "chroma"), settings=ChromaSettings(anonymized_telemetry=False)
        )
        if self.active_name is None:
            with self.lock, self.db:
                self._rebuild()
        else:
            row = self.db.execute(
                "SELECT signature FROM revisions WHERE name=?", (self.active_name,)
            ).fetchone()
            if not maintenance and (not row or row[0] != self.embedder.signature):
                raise RuntimeError(
                    "Embedding configuration changed; rebuild the index before serving queries"
                )

    @property
    def active_name(self) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key='active' ").fetchone()
        return row[0] if row else None

    @staticmethod
    def fingerprint(content: str, title: str, kind: str) -> str:
        return hashlib.sha256(json.dumps([content, title, kind]).encode()).hexdigest()

    def _required_active(self) -> str:
        name = self.active_name
        if name is None:
            raise RuntimeError("No active retrieval index")
        return name

    def _rebuild(self) -> None:
        name = "macbot_" + uuid.uuid4().hex
        collection = self.client.create_collection(
            name=name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine", "signature": self.embedder.signature},
        )
        try:
            records = list(self.db.execute("SELECT * FROM documents ORDER BY id"))
            entries = []
            for row in records:
                for i, chunk in enumerate(self.embedder.chunks(row["content"])):
                    cache_key = hashlib.sha256(
                        (self.embedder.signature + chunk["content"]).encode()
                    ).hexdigest()
                    entries.append(
                        {
                            "id": f"{row['id']}:{i}",
                            "content": chunk["content"],
                            "key": cache_key,
                            "metadata": {
                                "document_id": row["id"],
                                "title": row["title"],
                                "type": row["type"],
                                "chunk": i,
                                "start": chunk["start"],
                                "end": chunk["end"],
                            },
                        }
                    )
            for start in range(0, len(entries), 32):
                batch = entries[start : start + 32]
                vectors = {}
                missing = []
                for entry in batch:
                    row = self.db.execute(
                        "SELECT vector FROM embeddings WHERE key=?", (entry["key"],)
                    ).fetchone()
                    if row:
                        vectors[entry["key"]] = np.frombuffer(row[0], dtype=np.float32)
                    else:
                        missing.append(entry)
                if missing:
                    for entry, vector in zip(
                        missing, self.embedder.encode([e["content"] for e in missing]), strict=True
                    ):
                        vectors[entry["key"]] = vector
                        self.db.execute(
                            "INSERT OR IGNORE INTO embeddings VALUES (?,?)",
                            (entry["key"], vector.tobytes()),
                        )
                collection.add(
                    ids=[e["id"] for e in batch],
                    documents=[e["content"] for e in batch],
                    metadatas=[e["metadata"] for e in batch],
                    embeddings=np.stack([vectors[e["key"]] for e in batch]),
                )
            if collection.count() != len(entries):
                raise RuntimeError("Index count verification failed")
            if entries:
                vector = self.embedder.encode([entries[0]["content"]])[0]
                check = collection.query(query_embeddings=[vector.tolist()], n_results=1)
                if not check["ids"][0]:
                    raise RuntimeError("Index retrieval verification failed")
            self.db.execute(
                "INSERT INTO revisions VALUES (?,?,?,?)",
                (
                    name,
                    self.embedder.signature,
                    time.time(),
                    json.dumps([r["id"] for r in records]),
                ),
            )
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('active',?)", (name,))
        except BaseException:
            self.client.delete_collection(name)
            raise

    def add(
        self,
        content: str,
        title: str,
        kind: str = "text",
        metadata: dict | None = None,
        doc_id: str | None = None,
    ) -> str:
        if not isinstance(content, str) or not content.strip() or len(content) > 1_000_000:
            raise ValueError("Document must contain 1–1000000 characters")
        if not isinstance(title, str) or not 0 < len(title) <= 255:
            raise ValueError("Title must contain 1–255 characters")
        if not isinstance(kind, str) or not 0 < len(kind) <= 64:
            raise ValueError("Document type must contain 1–64 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object")
        if len(json.dumps(metadata or {})) > 65536:
            raise ValueError("Document metadata exceeds 64 KiB")
        fingerprint = self.fingerprint(content, title, kind)
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT id FROM documents WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if row:
                return row[0]
            doc_id = doc_id or uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?)",
                (doc_id, content, title, kind, json.dumps(metadata or {}), fingerprint),
            )
            self._rebuild()
        return doc_id

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 2000
            or type(top_k) is not int
            or not 1 <= top_k <= 20
        ):
            raise ValueError("Invalid search query or result count")
        with self.lock:
            collection = self.client.get_collection(
                self._required_active(), embedding_function=None
            )
            if not collection.count():
                return []
            result = collection.query(
                query_embeddings=self.embedder.encode([query]).tolist(),
                n_results=min(top_k, collection.count()),
            )
            texts, metadata, distances = (
                result["documents"],
                result["metadatas"],
                result["distances"],
            )
            if texts is None or metadata is None or distances is None:
                raise RuntimeError("Incomplete retrieval result")
            return [
                {
                    "id": doc_id,
                    "content": texts[0][i],
                    "metadata": metadata[0][i],
                    "distance": distances[0][i],
                    "score": 1 - distances[0][i],
                }
                for i, doc_id in enumerate(result["ids"][0])
            ]

    def list(self) -> list[dict]:
        with self.lock:
            return [
                {"id": r["id"], "title": r["title"], "type": r["type"], "length": len(r["content"])}
                for r in self.db.execute("SELECT * FROM documents ORDER BY title")
            ]

    def get(self, doc_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["metadata"] = json.loads(result["metadata"])
            return result

    def delete(self, doc_id: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            if not cursor.rowcount:
                return False
            self._rebuild()
        return True

    def rebuild(self):
        with self.lock, self.db:
            self._rebuild()

    def backup(self) -> Path:
        destination = self.settings.data_dir / "backups" / ("rag-" + uuid.uuid4().hex)
        destination.mkdir(parents=True, mode=0o700)
        with self.lock:
            with sqlite3.connect(destination / "documents.sqlite3") as target:
                self.db.backup(target)
            shutil.copytree(self.root / "chroma", destination / "chroma")
        return destination

    def migrate(self, source: Path) -> dict:
        source = source.expanduser().resolve()
        if source == self.root or self.root in source.parents or source in self.root.parents:
            raise ValueError("Migration source must be separate from the destination")
        backup = self.settings.data_dir / "backups" / ("legacy-" + uuid.uuid4().hex)
        shutil.copytree(source, backup)
        payload = (
            json.loads((backup / "documents.json").read_text())
            if (backup / "documents.json").exists()
            else {}
        )
        documents = dict(payload.get("documents", {}))
        metadata = dict(payload.get("metadata", {}))
        chroma_path = backup / "chroma_db"
        if chroma_path.exists():
            # Open a disposable inspection copy; the untouched backup remains restorable.
            import chromadb

            inspection = backup.with_name(backup.name + "-inspection")
            shutil.copytree(chroma_path, inspection)
            legacy = chromadb.PersistentClient(path=str(inspection))
            for collection in legacy.list_collections():
                result = legacy.get_collection(collection.name, embedding_function=None).get()
                for i, doc_id in enumerate(result["ids"]):
                    content = (result.get("documents") or [])[i]
                    if doc_id in documents and documents[doc_id] != content:
                        raise ValueError(
                            f"Legacy JSON/index content conflict for document {doc_id}; backup retained"
                        )
                    documents[doc_id] = content
                    index_meta = (result.get("metadatas") or [{}])[i] or {}
                    if doc_id in metadata and any(
                        k in metadata[doc_id] and metadata[doc_id][k] != v
                        for k, v in index_meta.items()
                    ):
                        raise ValueError(f"Legacy metadata conflict for {doc_id}; backup retained")
                    metadata.setdefault(doc_id, {}).update(index_meta)
        if not documents:
            raise ValueError("No source documents found; no migration performed")
        before = self.backup()
        with self.lock, self.db:
            for doc_id, content in documents.items():
                old = self.get(doc_id)
                if old and old["content"] != content:
                    raise ValueError(f"Destination content conflict for {doc_id}")
                if old:
                    continue
                meta = metadata.get(doc_id, {})
                if (
                    not isinstance(meta, dict)
                    or not isinstance(content, str)
                    or not content.strip()
                ):
                    raise ValueError(
                        f"Invalid source content or metadata for {doc_id}; backup retained"
                    )
                title, kind = meta.get("title", doc_id), meta.get("type", "text")
                if not isinstance(title, str) or not isinstance(kind, str):
                    raise ValueError(f"Invalid source title/type for {doc_id}; backup retained")
                # Legacy IDs are authoritative, including duplicate source records.
                # Use a distinct migration fingerprint when a duplicate already exists.
                fingerprint = self.fingerprint(content, title, kind)
                if self.db.execute(
                    "SELECT 1 FROM documents WHERE fingerprint=?", (fingerprint,)
                ).fetchone():
                    fingerprint = hashlib.sha256((fingerprint + ":" + doc_id).encode()).hexdigest()
                self.db.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?)",
                    (doc_id, content, title, kind, json.dumps(meta), fingerprint),
                )
            self._rebuild()
        return {
            "imported": len(documents),
            "source_backup": str(backup),
            "rollback_backup": str(before),
        }

    def stats(self):
        with self.lock:
            return {
                "documents": len(self.list()),
                "chunks": self.client.get_collection(
                    self._required_active(), embedding_function=None
                ).count(),
                "index": self.active_name,
                "embedding": self.embedder.signature,
            }

    def close(self):
        self.db.close()
        self.owner.close()


def restore(settings: Settings, backup: Path) -> Path:
    """Stage and verify an offline backup before replacing the active store."""
    backup = backup.expanduser().resolve()
    if (
        not (backup / "documents.sqlite3").is_file()
        or not (backup / "chroma/chroma.sqlite3").is_file()
    ):
        raise ValueError("Not a complete RAG backup")
    stage = settings.data_dir / (".rag-restore-" + uuid.uuid4().hex)
    previous = settings.data_dir / "backups" / ("before-restore-" + uuid.uuid4().hex)
    current = settings.data_dir / "rag"
    with (settings.data_dir / "rag.lock").open("a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Stop the document store before restoring") from None
        shutil.copytree(backup, stage)
        try:
            for path in [stage / "documents.sqlite3", stage / "chroma/chroma.sqlite3"]:
                with sqlite3.connect(path) as db:
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("Backup integrity check failed")
            with sqlite3.connect(stage / "documents.sqlite3") as db:
                active = db.execute("SELECT value FROM state WHERE key='active'").fetchone()
                if not active:
                    raise ValueError("Backup has no active index")
            with sqlite3.connect(stage / "chroma/chroma.sqlite3") as db:
                if not db.execute(
                    "SELECT 1 FROM collections WHERE name=?", (active[0],)
                ).fetchone():
                    raise ValueError("Backup active index is missing")
            previous.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if current.exists():
                current.rename(previous)
            try:
                os.replace(stage, current)
            except BaseException:
                if previous.exists():
                    previous.rename(current)
                raise
        except BaseException:
            # A failed staged copy is retained for diagnosis; the active store is untouched.
            raise
    return previous

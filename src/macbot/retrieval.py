"""Transactional documents with a versioned exact local vector index."""

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
from typing import Any

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

    def chunks(self, content: str) -> list[dict[str, Any]]:
        tokens = self.tokenizer.encode(content, add_special_tokens=False)
        chunks: list[dict[str, Any]] = []
        for index in range(0, len(tokens.ids), 192):
            selected = tokens.offsets[index : index + 224]
            if not selected:
                break
            start, end = selected[0][0], selected[-1][1]
            chunks.append({"content": content[start:end], "start": start, "end": end})
            if index + 224 >= len(tokens.ids):
                break
        return chunks

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 384), dtype=np.float32)
        with self.lock:
            encoded = self.encoder.encode_batch(texts)
            inputs = {
                "input_ids": np.array([item.ids for item in encoded], dtype=np.int64),
                "attention_mask": np.array(
                    [item.attention_mask for item in encoded], dtype=np.int64
                ),
                "token_type_ids": np.array([item.type_ids for item in encoded], dtype=np.int64),
            }
            output = self.session.run(
                None, {item.name: inputs[item.name] for item in self.session.get_inputs()}
            )[0]
            if output.ndim == 3:
                mask = inputs["attention_mask"][..., None]
                output = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            output = output.astype(np.float32)
            return output / np.maximum(np.linalg.norm(output, axis=1, keepdims=True), 1e-12)


class DocumentStore:
    def __init__(self, settings: Settings, *, maintenance: bool = False):
        self.settings = settings
        self.root = settings.data_dir / "rag"
        self.indexes = self.root / "indexes"
        self.indexes.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, content TEXT NOT NULL, title TEXT NOT NULL,
                type TEXT NOT NULL, metadata TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS embeddings(key TEXT PRIMARY KEY, vector BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions(
                name TEXT PRIMARY KEY, signature TEXT NOT NULL, created REAL NOT NULL,
                document_ids TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(revisions)")}
        if "chunk_count" not in columns:
            self.db.execute(
                "ALTER TABLE revisions ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 0"
            )
        self.db.commit()
        os.chmod(self.root / "documents.sqlite3", 0o600)
        self.embedder = Embedder(settings)
        self.vectors: np.ndarray = np.empty((0, 384), dtype=np.float32)
        self.chunks: list[dict[str, Any]] = []
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
            self._load(self._required_active())

    @property
    def active_name(self) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key='active'").fetchone()
        return row[0] if row else None

    @staticmethod
    def fingerprint(content: str, title: str, kind: str) -> str:
        return hashlib.sha256(json.dumps([content, title, kind]).encode()).hexdigest()

    def _required_active(self) -> str:
        name = self.active_name
        if name is None:
            raise RuntimeError("No active retrieval index")
        return name

    def _load(self, name: str) -> None:
        root = self.indexes / name
        vectors_path = root / "vectors.npy"
        chunks_path = root / "chunks.json"
        if not vectors_path.is_file() or not chunks_path.is_file():
            raise RuntimeError("Active retrieval index is incomplete")
        vectors = np.load(vectors_path, mmap_mode="r", allow_pickle=False)
        chunks = json.loads(chunks_path.read_text())
        if vectors.ndim != 2 or not isinstance(chunks, list) or len(vectors) != len(chunks):
            raise RuntimeError("Active retrieval index count verification failed")
        self.vectors = vectors
        self.chunks = chunks

    def _cached_vectors(self, entries: list[dict[str, Any]]) -> np.ndarray:
        vectors: dict[str, np.ndarray] = {}
        missing: list[dict[str, Any]] = []
        for entry in entries:
            row = self.db.execute(
                "SELECT vector FROM embeddings WHERE key=?", (entry["key"],)
            ).fetchone()
            if row:
                vectors[entry["key"]] = np.frombuffer(row[0], dtype=np.float32)
            else:
                missing.append(entry)
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            encoded = self.embedder.encode([item["content"] for item in batch])
            for entry, vector in zip(batch, encoded, strict=True):
                vectors[entry["key"]] = vector
                self.db.execute(
                    "INSERT OR IGNORE INTO embeddings VALUES (?,?)",
                    (entry["key"], vector.tobytes()),
                )
        if not entries:
            return np.empty((0, 384), dtype=np.float32)
        return np.stack([vectors[entry["key"]] for entry in entries]).astype(np.float32)

    def _rebuild(self) -> None:
        name = "exact_" + uuid.uuid4().hex
        stage = self.indexes / ("." + name + ".stage")
        final = self.indexes / name
        stage.mkdir(mode=0o700)
        records = list(self.db.execute("SELECT * FROM documents ORDER BY id"))
        entries: list[dict[str, Any]] = []
        for row in records:
            for index, chunk in enumerate(self.embedder.chunks(row["content"])):
                entries.append(
                    {
                        "id": f"{row['id']}:{index}",
                        "content": chunk["content"],
                        "key": hashlib.sha256(
                            (self.embedder.signature + chunk["content"]).encode()
                        ).hexdigest(),
                        "metadata": {
                            "document_id": row["id"],
                            "title": row["title"],
                            "type": row["type"],
                            "chunk": index,
                            "start": chunk["start"],
                            "end": chunk["end"],
                        },
                    }
                )
        try:
            vectors = self._cached_vectors(entries)
            with (stage / "vectors.npy").open("wb") as output:
                np.save(output, vectors, allow_pickle=False)
                output.flush()
                os.fsync(output.fileno())
            payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
            with (stage / "chunks.json").open("wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(stage, final)
            self.db.execute(
                "INSERT INTO revisions(name,signature,created,document_ids,chunk_count) VALUES(?,?,?,?,?)",
                (
                    name,
                    self.embedder.signature,
                    time.time(),
                    json.dumps([row["id"] for row in records]),
                    len(entries),
                ),
            )
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('active',?)", (name,))
            self._load(name)
            if entries:
                result = self.search(entries[0]["content"], 1)
                if not result or result[0]["score"] < 0.99:
                    raise RuntimeError("Index retrieval verification failed")
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            if final.exists() and self.active_name != name:
                shutil.rmtree(final)
            raise

    def add(
        self,
        content: str,
        title: str,
        kind: str = "text",
        metadata: dict[str, Any] | None = None,
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
        if len(json.dumps(metadata or {})) > 65_536:
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

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 2000
            or type(top_k) is not int
            or not 1 <= top_k <= 20
        ):
            raise ValueError("Invalid search query or result count")
        with self.lock:
            if not len(self.chunks):
                return []
            vector = self.embedder.encode([query])[0]
            scores = np.asarray(self.vectors @ vector, dtype=np.float32)
            count = min(top_k, len(scores))
            indices = np.argpartition(scores, -count)[-count:]
            indices = indices[np.argsort(scores[indices])[::-1]]
            return [
                {
                    "id": self.chunks[int(index)]["id"],
                    "content": self.chunks[int(index)]["content"],
                    "metadata": self.chunks[int(index)]["metadata"],
                    "distance": float(1 - scores[int(index)]),
                    "score": float(scores[int(index)]),
                }
                for index in indices
            ]

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "type": row["type"],
                    "length": len(row["content"]),
                }
                for row in self.db.execute("SELECT * FROM documents ORDER BY title")
            ]

    def get(self, doc_id: str) -> dict[str, Any] | None:
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

    def rebuild(self) -> None:
        with self.lock, self.db:
            self._rebuild()

    def revision_count(self, name: str) -> int:
        row = self.db.execute("SELECT chunk_count FROM revisions WHERE name=?", (name,)).fetchone()
        if not row:
            raise ValueError("Unknown retrieval revision")
        return int(row[0])

    def backup(self) -> Path:
        destination = self.settings.data_dir / "backups" / ("rag-" + uuid.uuid4().hex)
        destination.mkdir(parents=True, mode=0o700)
        with self.lock:
            with sqlite3.connect(destination / "documents.sqlite3") as target:
                self.db.backup(target)
            shutil.copytree(self.indexes, destination / "indexes")
        return destination

    def migrate(self, source: Path) -> dict[str, Any]:
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
        source_db = backup / "documents.sqlite3"
        if source_db.is_file():
            with sqlite3.connect(source_db) as legacy_db:
                legacy_db.row_factory = sqlite3.Row
                for row in legacy_db.execute(
                    "SELECT id,content,title,type,metadata FROM documents"
                ):
                    if row["id"] in documents and documents[row["id"]] != row["content"]:
                        raise ValueError(f"Legacy content conflict for document {row['id']}")
                    documents[row["id"]] = row["content"]
                    item = json.loads(row["metadata"])
                    item.update(title=row["title"], type=row["type"])
                    metadata.setdefault(row["id"], {}).update(item)
        if not documents:
            raise ValueError(
                "No authoritative source documents found; preserve the backup and export legacy Chroma before migration"
            )
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
                    raise ValueError(f"Invalid source content or metadata for {doc_id}")
                title, kind = meta.get("title", doc_id), meta.get("type", "text")
                if not isinstance(title, str) or not isinstance(kind, str):
                    raise ValueError(f"Invalid source title/type for {doc_id}")
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

    def stats(self) -> dict[str, Any]:
        with self.lock:
            return {
                "documents": len(self.list()),
                "chunks": len(self.chunks),
                "index": self.active_name,
                "index_type": "exact_cosine_mmap",
                "embedding": self.embedder.signature,
            }

    def close(self) -> None:
        self.vectors = np.empty((0, 384), dtype=np.float32)
        self.chunks = []
        self.db.close()
        self.owner.close()


def restore(settings: Settings, backup: Path) -> Path:
    """Stage and verify an offline backup before replacing the active store."""
    backup = backup.expanduser().resolve()
    if not (backup / "documents.sqlite3").is_file() or not (backup / "indexes").is_dir():
        raise ValueError("Not a complete exact-index RAG backup")
    stage = settings.data_dir / (".rag-restore-" + uuid.uuid4().hex)
    previous = settings.data_dir / "backups" / ("before-restore-" + uuid.uuid4().hex)
    current = settings.data_dir / "rag"
    with (settings.data_dir / "rag.lock").open("a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Stop the document store before restoring") from None
        shutil.copytree(backup, stage)
        with sqlite3.connect(stage / "documents.sqlite3") as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
            active = db.execute("SELECT value FROM state WHERE key='active'").fetchone()
            if not active:
                raise ValueError("Backup has no active index")
            expected = db.execute(
                "SELECT chunk_count FROM revisions WHERE name=?", (active[0],)
            ).fetchone()
        root = stage / "indexes" / active[0]
        vectors = np.load(root / "vectors.npy", mmap_mode="r", allow_pickle=False)
        chunks = json.loads((root / "chunks.json").read_text())
        if not expected or len(vectors) != expected[0] or len(chunks) != expected[0]:
            raise ValueError("Backup index count verification failed")
        previous.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if current.exists():
            current.rename(previous)
        try:
            os.replace(stage, current)
        except BaseException:
            if previous.exists():
                previous.rename(current)
            raise
    return previous

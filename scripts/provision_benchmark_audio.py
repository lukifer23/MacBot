#!/usr/bin/env python3
"""Explicit download of real LibriSpeech recordings, pinned to a public HF subset."""

import hashlib
import io
import json

import httpx
import pyarrow.parquet as pq
import soundfile as sf

from macbot.config import atomic_write, load

REPO = "hf-internal-testing/librispeech_asr_dummy"
REVISION = "5be91486e11a2d616f4ec5db8d3fd248585ac07a"
FILE = "clean/validation-00000-of-00001.parquet"
SHA256 = "4e69a06fa5edc90921e5e7e39a7084881f8b3ed9c805c574f4f39c6fde27c603"
root = load().data_dir / "benchmarks/librispeech"
root.mkdir(parents=True, exist_ok=True, mode=0o700)
url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{FILE}"
path = root / "source.parquet"
if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
    response = httpx.get(url, follow_redirects=True, timeout=60, trust_env=False)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != SHA256:
        raise ValueError("Corpus checksum mismatch")
    atomic_write(path, response.content)
rows = pq.read_table(path).to_pylist()
records = []
for row in rows:
    content = row["audio"]["bytes"]
    info = sf.info(io.BytesIO(content))
    if info.duration > 12:
        continue
    destination = root / (row["id"] + ".flac")
    atomic_write(destination, content)
    records.append(
        {
            "id": row["id"],
            "file": destination.name,
            "transcript": row["text"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "duration_seconds": info.duration,
            "sample_rate": info.samplerate,
        }
    )
    if len(records) == 20:
        break
manifest = {
    "repository": REPO,
    "revision": REVISION,
    "source_file": FILE,
    "source_sha256": SHA256,
    "source_url": url,
    "license": "CC-BY-4.0",
    "license_source": "https://www.openslr.org/12/",
    "scope": "20 real LibriSpeech read-speech recordings, one speaker, first rows <=12 seconds. Not conversational microphone/AEC acceptance.",
    "records": records,
}
atomic_write(root / "manifest.json", json.dumps(manifest, indent=2).encode())
print(root / "manifest.json")

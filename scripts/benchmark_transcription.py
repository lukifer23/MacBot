#!/usr/bin/env python3
"""Resident STT benchmark using pinned real recordings and reference transcripts."""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import psutil
import soundfile as sf

from macbot.config import load
from macbot.provision import verify
from macbot.speech import Transcriber


def words(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def distance(expected, actual):
    row = list(range(len(actual) + 1))
    for i, a in enumerate(expected, 1):
        following = [i]
        for j, b in enumerate(actual, 1):
            following.append(min(row[j] + 1, following[j - 1] + 1, row[j - 1] + (a != b)))
        row = following
    return row[-1]


def run(backend, output):
    s = load()
    s.models.stt = backend
    root = s.data_dir / "benchmarks/librispeech"
    manifest = json.loads((root / "manifest.json").read_text())
    receipt = verify(s, "parakeet" if backend == "parakeet" else "whisper-base")
    start = time.monotonic()
    stt = Transcriber(s)
    load_ms = (time.monotonic() - start) * 1000
    results = []
    try:
        for repeat in range(2):
            for record in manifest["records"]:
                path = root / record["file"]
                assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
                audio, sr = sf.read(path, dtype="float32")
                assert sr == 16000 and audio.ndim == 1
                start = time.monotonic()
                text = stt.transcribe(audio)
                elapsed = (time.monotonic() - start) * 1000
                expected = words(record["transcript"])
                actual = words(text)
                processes = [psutil.Process()]
                if stt.worker:
                    processes.append(psutil.Process(stt.worker.pid))
                result = {
                    "id": record["id"],
                    "repeat": repeat,
                    "duration_seconds": len(audio) / sr,
                    "elapsed_ms": elapsed,
                    "reference": record["transcript"],
                    "transcript": text,
                    "word_errors": distance(expected, actual),
                    "reference_words": len(expected),
                    "rss_bytes": sum(p.memory_info().rss for p in processes),
                }
                results.append(result)
                with output.open("a") as f:
                    f.write(json.dumps(result) + "\n")
                print(
                    backend,
                    repeat,
                    record["id"],
                    round(elapsed, 1),
                    result["word_errors"],
                    flush=True,
                )
        warm = [r for r in results if r["repeat"] == 1]
        times = sorted(r["elapsed_ms"] for r in warm)
        summary = {
            "backend": backend,
            "model": receipt,
            "corpus_revision": manifest["revision"],
            "load_ms": load_ms,
            "first_inference_ms": results[0]["elapsed_ms"],
            "warm_p95_ms": times[18],
            "warm_wer": sum(r["word_errors"] for r in warm)
            / sum(r["reference_words"] for r in warm),
            "max_rss_bytes": max(r["rss_bytes"] for r in results),
            "scope": manifest["scope"],
        }
        output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
    finally:
        stt.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("backend", choices=["parakeet", "whisper"])
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(args.backend, args.output)

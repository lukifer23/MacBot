#!/usr/bin/env python3
"""Benchmark real resident TTS candidates and retain listenable evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import statistics
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

from macbot.config import load
from macbot.provision import catalog, sha256, verify, voice_model
from macbot.residency import InferenceResidencyLease
from macbot.speech import Synthesizer

PASSAGES = [
    "Good morning. I checked the local services, and everything is ready for you.",
    "The forecast calls for scattered rain after three thirty, with calmer wind tonight.",
    "Your document says the verification phrase is cobalt lantern.",
    "I opened Calculator and Notes. Both actions completed successfully.",
    "Dr. Rivera scheduled the follow-up for February twenty-first at 10:45 a.m.",
    "I couldn't reach the search provider, so I don't have a trustworthy result yet.",
    "Sure. We can take this one step at a time, and you can interrupt me whenever you need.",
    "The total is one thousand, two hundred forty-seven dollars and ninety-six cents.",
]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(np.ceil(len(ordered) * fraction)) - 1))]


def _run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("voice")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix("").is_dir():
        raise SystemExit("Refusing to overwrite benchmark evidence")

    settings = load()
    settings.models.tts_voice = args.voice
    model_name = voice_model(args.voice)
    verification = verify(settings, model_name)
    item = catalog()[model_name]
    artifact_hashes = {
        entry["name"]: sha256(settings.data_dir / "models" / model_name / entry["name"])
        for entry in item["files"]
    }
    started = time.perf_counter()
    synth = Synthesizer(settings)
    load_seconds = time.perf_counter() - started
    audio_dir = args.output.with_suffix("")
    audio_dir.mkdir(parents=True, mode=0o700)
    process = psutil.Process()
    rows = []
    for index, text in enumerate(PASSAGES):
        cancel = threading.Event()
        turn_started = time.perf_counter()
        first = None
        chunks = []
        rate = None
        for samples, sample_rate in synth.chunks(text, cancel):
            if first is None:
                first = time.perf_counter()
            chunks.append(samples.copy())
            rate = sample_rate
        ended = time.perf_counter()
        if first is None or rate is None or not chunks:
            raise RuntimeError(f"No audio generated for passage {index}")
        audio = np.concatenate(chunks)
        audio_path = audio_dir / f"{index + 1:02d}.wav"
        sf.write(audio_path, audio, rate)
        rows.append(
            {
                "index": index,
                "text": text,
                "first_chunk_ms": (first - turn_started) * 1000,
                "total_ms": (ended - turn_started) * 1000,
                "duration_s": len(audio) / rate,
                "sample_rate": rate,
                "chunks": len(chunks),
                "rss_bytes": process.memory_info().rss,
                "audio": str(audio_path),
                "audio_sha256": sha256(audio_path),
            }
        )
    first_chunks = [row["first_chunk_ms"] for row in rows]
    report = {
        "voice": args.voice,
        "model": model_name,
        "source_repo": item["repo"],
        "source_revision": verification["revision"],
        "conversion": item["conversion"],
        "artifact_hashes": artifact_hashes,
        "runtime": {"mlx-audio": importlib.metadata.version("mlx-audio")},
        "cold_load_ms": load_seconds * 1000,
        "first_chunk_p50_ms": statistics.median(first_chunks),
        "first_chunk_p95_ms": percentile(first_chunks, 0.95),
        "maximum_sampled_rss_bytes": max(row["rss_bytes"] for row in rows),
        "rows": rows,
        "limitations": [
            "Software synthesis timing is not speech-end-to-audible latency.",
            "Listening quality and 30-minute stability require operator acceptance.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "voice",
                    "cold_load_ms",
                    "first_chunk_p50_ms",
                    "first_chunk_p95_ms",
                    "maximum_sampled_rss_bytes",
                )
            },
            indent=2,
        )
    )


def main() -> None:
    settings = load()
    with InferenceResidencyLease(settings.data_dir, purpose="tts-benchmark"):
        _run()


if __name__ == "__main__":
    main()

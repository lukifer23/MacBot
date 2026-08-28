"""Owned native audio process with bounded capture and playback credits."""

from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import threading
import time
from typing import Callable

import numpy as np

from .config import Settings
from .pipe_io import write_all
from .provision import native_binary


def read_exact(stream, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        block = stream.read(size - len(result))
        if not block:
            raise EOFError("Audio helper closed its pipe")
        result.extend(block)
    return bytes(result)


class NativeAudio:
    def __init__(self, settings: Settings, on_event: Callable[[dict], None] | None = None):
        self.settings = settings
        self.on_event = on_event or (lambda event: None)
        self.process: subprocess.Popen | None = None
        self.write_lock = threading.Lock()
        self.condition = threading.Condition()
        self.capture: queue.Queue[np.ndarray] = queue.Queue(maxsize=32)
        self.generation = 0
        self.inflight = 0
        self.ready = False
        self.aec = False
        self.error: str | None = None
        self.dropped = 0
        self.reader: threading.Thread | None = None
        self.last_stop_ns = 0
        self.closing = False
        self.input_peak = 0.0
        self.input_rms = 0.0
        self.input_frames = 0
        self.input_updated = 0.0

    def input_status(self) -> dict:
        with self.condition:
            age = time.monotonic() - self.input_updated if self.input_updated else None
            fresh = age is not None and age < 1
            return {
                "peak": self.input_peak if fresh else 0.0,
                "rms": self.input_rms if fresh else 0.0,
                "frames": self.input_frames,
                "age_ms": age * 1000 if age is not None else None,
                "receiving": fresh,
            }

    def launch(self, capture: bool = False) -> None:
        try:
            self._launch(capture)
        except Exception:
            self.close()
            raise

    def _launch(self, capture: bool) -> None:
        with self.condition:
            if self.process and self.process.poll() is None:
                self.command("capture", enabled=capture)
                return
            self.ready = self.aec = False
            self.closing = False
            self.error = None
            self.inflight = 0
            self.input_frames = 0
            self.input_updated = 0.0
            logfile = self.settings.data_dir / "logs" / "audio.log"
            with logfile.open("ab") as log:
                self.process = subprocess.Popen(
                    [str(native_binary(self.settings))],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    bufsize=0,
                )
            assert self.process.stdin is not None
            os.set_blocking(self.process.stdin.fileno(), False)
            self.reader = threading.Thread(
                target=self._read, args=(self.process,), name="native-audio", daemon=True
            )
            self.reader.start()
            self.command("start", capture=capture, generation=self.generation)
            self.condition.wait_for(lambda: self.ready or self.error is not None, timeout=10)
            error = (
                None
                if self.ready
                else (
                    self.error or "Audio helper did not become ready; check microphone permission"
                )
            )
        if error:
            self.close()
            raise RuntimeError(error)

    def _write(self, kind: int, payload: bytes) -> None:
        with self.write_lock:
            if not self.process or not self.process.stdin or self.process.poll() is not None:
                raise RuntimeError("Audio helper is not running")
            packet = struct.pack(">IB", len(payload) + 1, kind) + payload
            write_all(self.process.stdin, packet, 0.2)

    def command(self, op: str, **kwargs) -> None:
        self._write(1, json.dumps({"op": op, **kwargs}).encode())

    def _read(self, process: subprocess.Popen):
        try:
            assert process.stdout
            while True:
                size = struct.unpack(">I", read_exact(process.stdout, 4))[0]
                if size < 1 or size > 512 * 1024:
                    raise ValueError("Invalid native audio frame")
                frame = read_exact(process.stdout, size)
                if frame[0] == 2:
                    samples = np.frombuffer(frame[1:], dtype="<f4").copy()
                    if not samples.size or not np.isfinite(samples).all():
                        raise ValueError("Invalid microphone samples")
                    with self.condition:
                        self.input_peak = float(np.max(np.abs(samples)))
                        self.input_rms = float(np.sqrt(np.mean(np.square(samples))))
                        self.input_frames += 1
                        self.input_updated = time.monotonic()
                    try:
                        self.capture.put_nowait(samples)
                    except queue.Full:
                        self.dropped += 1
                        self.on_event({"event": "overflow", "frames": 1})
                elif frame[0] == 1:
                    data = json.loads(frame[1:])
                    with self.condition:
                        if data["event"] == "ready":
                            if data.get("protocol") != 2:
                                raise ValueError(
                                    "Audio helper is outdated. Run macbot build-audio and restart."
                                )
                            self.ready, self.aec = True, bool(data.get("aec"))
                        elif data["event"] == "error":
                            self.error = data.get("message", "Audio error")
                        elif (
                            data["event"] == "played" and data.get("generation") == self.generation
                        ):
                            self.inflight = max(0, self.inflight - 1)
                        elif data["event"] == "stopped":
                            self.last_stop_ns = data["time_ns"]
                        self.condition.notify_all()
                    self.on_event(data)
        except (EOFError, OSError, ValueError) as exc:
            with self.condition:
                self.ready = False
                self.error = None if self.closing else str(exc)
                self.condition.notify_all()
            if not self.closing:
                self.on_event({"event": "error", "message": str(exc)})

    def play(
        self, samples: np.ndarray, sample_rate: int, cancel: threading.Event, generation: int
    ) -> None:
        playback_rate = 48000
        if sample_rate != playback_rate:
            # Preserve the voice bandwidth; 16 kHz is only the STT capture rate.
            from math import gcd

            from scipy.signal import resample_poly

            common = gcd(sample_rate, playback_rate)
            samples = resample_poly(samples, playback_rate // common, sample_rate // common)
        pcm = np.clip(samples, -1, 1).astype("<f4")
        chunk_size = playback_rate // 20
        for offset in range(0, len(pcm), chunk_size):
            with self.condition:
                deadline = time.monotonic() + 5
                while self.inflight >= 4 and not cancel.is_set() and generation == self.generation:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Audio playback credits timed out")
                    self.condition.wait(0.025)
                if cancel.is_set() or generation != self.generation:
                    return
                if not self.ready or self.error:
                    raise RuntimeError(self.error or "Audio helper unavailable")
                self.inflight += 1
                self._write(
                    2,
                    struct.pack(">QI", generation, playback_rate)
                    + pcm[offset : offset + chunk_size].tobytes(),
                )

    def drain(self, cancel: threading.Event, timeout: float = 10) -> None:
        with self.condition:
            done = self.condition.wait_for(
                lambda: self.inflight == 0 or cancel.is_set() or self.error, timeout
            )
            if not done or self.error:
                raise RuntimeError(self.error or "Playback did not drain")

    def cancel(self) -> int:
        with self.condition:
            self.generation += 1
            self.inflight = 0
            if self.process and self.process.poll() is None:
                self.command("cancel", generation=self.generation)
            self.condition.notify_all()
            return self.generation

    def close(self):
        self.closing = True
        if not self.process:
            return
        if self.process.poll() is None:
            try:
                self.command("shutdown")
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired, RuntimeError):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        if self.reader:
            self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()
        self.process = None
        self.ready = False
        self.aec = False
        while True:
            try:
                self.capture.get_nowait()
            except queue.Empty:
                break

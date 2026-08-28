"""Resident, explicitly selected speech models. No fallback or import-time loading."""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Settings
from .pipe_io import read_exact, write_all
from .provision import model_dir, model_file


class SileroVAD:
    def __init__(self, settings: Settings):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_file(settings, "silero", ".onnx")),
            options,
            providers=["CPUExecutionProvider"],
        )
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, frame: np.ndarray) -> float:
        if frame.size != 512:
            raise ValueError("Silero requires exactly 512 mono samples at 16 kHz")
        audio = np.concatenate((self.context, frame.reshape(1, 512)), axis=1).astype(np.float32)
        output, self.state = self.session.run(
            None, {"input": audio, "state": self.state, "sr": np.array(16000, dtype=np.int64)}
        )
        self.context = audio[:, -64:].copy()
        return float(output.item())


class Transcriber:
    def __init__(self, settings: Settings):
        os.environ["HF_HUB_OFFLINE"] = "1"
        self.settings = settings
        self.lock = threading.Lock()
        self.model = None
        self.worker = None
        if settings.models.stt == "parakeet":
            import mlx.core as mx
            from parakeet_mlx import from_pretrained

            self.model = from_pretrained(str(model_dir(settings, "parakeet")))
            mx.eval(self.model.parameters())
        else:
            binary = settings.data_dir / "bin" / "macbot-whisper"
            if not binary.exists():
                raise FileNotFoundError(
                    "Run macbot build-inference to build the resident Whisper worker"
                )
            with (settings.data_dir / "logs" / "whisper.log").open("ab") as log:
                self.worker = subprocess.Popen(
                    [str(binary), str(model_file(settings, "whisper-base", ".bin"))],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    bufsize=0,
                )
            assert self.worker.stdin is not None
            os.set_blocking(self.worker.stdin.fileno(), False)
            if self._response() != "ready":
                raise RuntimeError("Whisper model initialization failed")

    def _response(self) -> str:
        assert self.worker and self.worker.stdout
        size = struct.unpack(">I", read_exact(self.worker.stdout, 4, 30))[0]
        if size > 1024 * 1024:
            raise ValueError("Whisper response exceeds limit")
        return read_exact(self.worker.stdout, size, 3).decode()

    def transcribe(self, audio: np.ndarray) -> str:
        if not 0 < len(audio) <= self.settings.audio.max_utterance_sec * 16000:
            raise ValueError("Audio duration outside configured range")
        audio = np.clip(audio, -1, 1).astype(np.float32)
        with self.lock:
            if self.model is not None:
                import mlx.core as mx
                from parakeet_mlx.audio import get_logmel

                mel = get_logmel(mx.array(audio), self.model.preprocessor_config)
                return self.model.generate(mel)[0].text.strip()
            assert self.worker and self.worker.stdin
            data = audio.astype("<f4").tobytes()
            write_all(self.worker.stdin, struct.pack(">I", len(data)) + data, 5)
            text = self._response()
            if text.startswith("ERROR:"):
                raise RuntimeError(text)
            return text.strip()

    def decode(self, content: bytes, suffix: str) -> np.ndarray:
        # Decoder input is a private file, never an attacker-controlled URL or protocol.
        with tempfile.TemporaryDirectory(prefix="macbot-audio-") as tmp:
            source = Path(tmp) / ("input" + suffix)
            output = Path(tmp) / "output.wav"
            source.write_bytes(content)
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(source),
                    "-t",
                    str(self.settings.audio.max_utterance_sec + 1),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ],
                capture_output=True,
                check=True,
                timeout=15,
            )
            audio, rate = sf.read(output, dtype="float32")
            if rate != 16000 or len(audio) > self.settings.audio.max_utterance_sec * 16000:
                raise ValueError("Audio exceeds duration limit")
            return audio

    def close(self):
        if self.worker:
            if self.worker.stdin:
                self.worker.stdin.close()
            try:
                self.worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait()
            if self.worker.stdout:
                self.worker.stdout.close()


class Synthesizer:
    def __init__(self, settings: Settings):
        from piper import PiperVoice

        self.settings = settings
        self.path = model_file(settings, settings.models.tts_voice, ".onnx")
        self.voice = PiperVoice.load(str(self.path))
        self.lock = threading.Lock()
        self.cache: OrderedDict[tuple, list[tuple[np.ndarray, int]]] = OrderedDict()
        self.cache_bytes = 0

    def chunks(self, text: str, cancel: threading.Event):
        from piper import SynthesisConfig

        key = (str(self.path), self.settings.models.tts_speed, text)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                for audio, sr in self.cache[key]:
                    if cancel.is_set():
                        return
                    yield audio, sr
                return
            chunks = []
            total = 0
            for chunk in self.voice.synthesize(
                text, SynthesisConfig(length_scale=1 / self.settings.models.tts_speed)
            ):
                if cancel.is_set():
                    return
                samples = chunk.audio_float_array.copy()
                total += samples.nbytes
                if total <= 2 * 1024 * 1024:
                    chunks.append((samples, chunk.sample_rate))
                yield samples, chunk.sample_rate
            if total <= 2 * 1024 * 1024:
                self.cache[key] = chunks
                self.cache_bytes += total
                while self.cache_bytes > 16 * 1024 * 1024 or len(self.cache) > 32:
                    _, old = self.cache.popitem(last=False)
                    self.cache_bytes -= sum(a.nbytes for a, _ in old)

"""Resident, explicitly selected speech models. No fallback or import-time loading."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Settings
from .provision import QWEN_TTS_VOICES, model_dir, model_file, voices


def split_speech(buffer: str, *, final: bool = False) -> tuple[list[str], str]:
    """Emit phrases at word boundaries, even when model tokens bisect a word."""
    phrases = []
    while buffer:
        cut = 0
        for match in re.finditer(r"[.!?][\"')\]]*(?=\s)", buffer):
            prefix = buffer[: match.start() + 1].split()
            word = prefix[-1].lower() if prefix else ""
            if word in {"mr.", "mrs.", "ms.", "dr.", "prof.", "e.g.", "i.e."}:
                continue
            if re.fullmatch(r"[a-z]\.", word):
                continue
            cut = match.end()
            break
        if not cut and len(buffer) >= 180:
            boundaries = list(re.finditer(r"[,;:](?=\s)", buffer[:180]))
            cut = next((m.end() for m in reversed(boundaries) if m.end() >= 60), 0)
            if not cut:
                spaces = list(re.finditer(r"\s+", buffer[:180]))
                cut = spaces[-1].end() if spaces else 0
        if not cut:
            if final:
                cut = len(buffer)
            else:
                break
        phrase, buffer = buffer[:cut].strip(), buffer[cut:].lstrip()
        if phrase:
            phrases.append(phrase)
    return phrases, buffer


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
        import mlx.core as mx
        from parakeet_mlx import from_pretrained

        self.model = from_pretrained(str(model_dir(settings, "parakeet")))
        mx.eval(self.model.parameters())

    def transcribe(self, audio: np.ndarray) -> str:
        if not 0 < len(audio) <= self.settings.audio.max_utterance_sec * 16000:
            raise ValueError("Audio duration outside configured range")
        audio = np.clip(audio, -1, 1).astype(np.float32)
        with self.lock:
            import mlx.core as mx
            from parakeet_mlx.audio import get_logmel

            mel = get_logmel(mx.array(audio), self.model.preprocessor_config)
            return self.model.generate(mel)[0].text.strip()

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
        pass


class Synthesizer:
    def __init__(self, settings: Settings):
        self.settings = settings
        name = settings.models.tts_voice
        if name not in voices():
            raise ValueError("TTS voice is not registered")
        self.voice_id = name
        if name not in QWEN_TTS_VOICES:
            raise ValueError("The selected production voice must use Qwen TTS")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from mlx_audio.tts.utils import load_model

        model_name, self.qwen_speaker = QWEN_TTS_VOICES[name]
        self.path = model_dir(settings, model_name)
        self.qwen = load_model(self.path)
        self.lock = threading.Lock()
        self.cache: OrderedDict[tuple, list[tuple[np.ndarray, int]]] = OrderedDict()
        self.cache_bytes = 0

    def _generate(self, text: str, cancel: threading.Event):
        generator = self.qwen.generate_custom_voice(
            text=text,
            speaker=self.qwen_speaker,
            language="English",
            instruct="Speak naturally, warmly, and conversationally.",
            stream=True,
            streaming_interval=0.32,
            temperature=0.8,
        )
        try:
            for result in generator:
                if cancel.is_set():
                    return
                samples = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                if samples.size:
                    yield samples, int(result.sample_rate)
        finally:
            generator.close()

    def chunks(self, text: str, cancel: threading.Event):
        key = (str(self.path), self.voice_id, text)
        with self.lock:
            if cancel.is_set():
                return
            if key in self.cache:
                self.cache.move_to_end(key)
                for audio, sr in self.cache[key]:
                    if cancel.is_set():
                        return
                    yield audio, sr
                return
            chunks = []
            total = 0
            for samples, rate in self._generate(text, cancel):
                if cancel.is_set():
                    return
                total += samples.nbytes
                if total <= 2 * 1024 * 1024:
                    chunks.append((samples, rate))
                yield samples, rate
            if total <= 2 * 1024 * 1024:
                self.cache[key] = chunks
                self.cache_bytes += total
                while self.cache_bytes > 16 * 1024 * 1024 or len(self.cache) > 32:
                    _, old = self.cache.popitem(last=False)
                    self.cache_bytes -= sum(a.nbytes for a, _ in old)

    @property
    def supports_speed(self) -> bool:
        return False

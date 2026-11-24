"""Resident in-process speech recognition using the pinned Cactus 4 ABI."""

from __future__ import annotations

import ctypes
import json
import platform
import tempfile
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from .config import Settings
from .provision import model_file


class Whistle:
    def __init__(self, settings: Settings):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise RuntimeError("Whistle requires an Apple Silicon Mac")
        self._weights = model_file(settings, "whistle", ".cact").read_bytes()
        wheel = model_file(settings, "whistle", ".whl")
        self._directory = tempfile.TemporaryDirectory(prefix="macbot-whistle-")
        try:
            with ZipFile(wheel) as archive:
                libraries = [entry for entry in archive.namelist() if entry.endswith(".dylib")]
                if len(libraries) != 1:
                    raise ValueError("Expected one native library in the pinned Cactus wheel")
                library_path = Path(self._directory.name) / Path(libraries[0]).name
                library_path.write_bytes(archive.read(libraries[0]))
            library = ctypes.CDLL(str(library_path))
            library.cactus_last_error.argtypes = []
            library.cactus_last_error.restype = ctypes.c_char_p
            library.cactus_load.argtypes = [ctypes.c_char_p, ctypes.c_ulonglong]
            library.cactus_load.restype = ctypes.c_int
            library.cactus_models.argtypes = []
            library.cactus_models.restype = ctypes.c_int
            library.cactus_transcribe.argtypes = [
                np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags="C_CONTIGUOUS"),
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_char),
                ctypes.c_int,
            ]
            library.cactus_transcribe.restype = ctypes.c_int
            self._library = library
            if library.cactus_load(self._weights, len(self._weights)) < 0 or not (
                library.cactus_models() & 2  # CACTUS_SPEECH from the 4.0.0 header.
            ):
                raise RuntimeError(f"Whistle could not load: {self._error()}")
        except BaseException:
            self._directory.cleanup()
            raise

    def _error(self) -> str:
        return (self._library.cactus_last_error() or b"unknown Cactus error").decode(
            "utf-8", errors="replace"
        )

    def transcribe(self, audio: np.ndarray) -> str:
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        result = ctypes.create_string_buffer(65536)
        size = self._library.cactus_transcribe(
            samples, samples.size, b"", b"", 0, result, len(result)
        )
        if size < 0 or size >= len(result):
            raise RuntimeError(f"Whistle transcription failed: {self._error()}")
        payload = json.loads(result.value)
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise RuntimeError("Whistle returned an invalid transcription")
        return str(payload["text"]).strip()

    def close(self) -> None:
        self._directory.cleanup()

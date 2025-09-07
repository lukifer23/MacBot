import os
import time
import numpy as np
import pathlib
import sys

# Ensure repository src is on path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("MACBOT_DISABLE_TTS", "1")

from macbot.voice_assistant import (  # type: ignore
    SAMPLE_RATE,
    _WHISPER_IMPL,
    transcribe,
    _transcribe_cli,
)


def benchmark() -> None:
    audio = np.random.randn(SAMPLE_RATE).astype(np.float32)

    start = time.perf_counter()
    transcribe(audio)
    end = time.perf_counter()
    print(f"in-memory ({_WHISPER_IMPL}): {end - start:.3f}s")

    start = time.perf_counter()
    _transcribe_cli(audio)
    end = time.perf_counter()
    print(f"cli fallback: {end - start:.3f}s")


if __name__ == "__main__":
    benchmark()

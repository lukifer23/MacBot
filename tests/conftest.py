import sys
import types


class _DummyStream:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _output_stream(*args, **kwargs):
    return _DummyStream()


sd_stub = types.SimpleNamespace(OutputStream=_output_stream)

sf_stub = types.ModuleType("soundfile")
sf_stub.write = lambda *a, **k: None

sys.modules.setdefault("sounddevice", sd_stub)
sys.modules.setdefault("soundfile", sf_stub)

# Stub TTS related optional dependencies used during import
sys.modules.setdefault("pyttsx3", types.SimpleNamespace(init=lambda: types.SimpleNamespace(setProperty=lambda *a, **k: None, say=lambda *a, **k: None, runAndWait=lambda: None)))
sys.modules.setdefault("kokoro", types.SimpleNamespace(KPipeline=lambda *a, **k: None))


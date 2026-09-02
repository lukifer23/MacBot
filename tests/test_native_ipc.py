import json
import os
import secrets
import socket
import struct
import threading
import time

import numpy as np

from macbot.config import Settings, prepare
from macbot.native_ipc import NativeIPCServer, read_frame, write_frame
from macbot.runtime import Runtime


def test_native_ipc_authentication_and_bounded_status(tmp_path):
    settings = Settings(data_dir=tmp_path, privacy={"history_enabled": False})
    prepare(settings)
    token = secrets.token_hex(32)
    token_path = tmp_path / "run/native-token"
    token_path.write_text(token)
    os.chmod(token_path, 0o600)
    runtime = Runtime(settings, load_speech=False)
    server = NativeIPCServer(settings, runtime)
    assert server.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(server.path))
            write_frame(client, {"op": "authenticate", "token": token, "protocol_version": 3})
            assert read_frame(client)["ok"]
            write_frame(client, {"op": "status", "protocol_version": 3})
            response = read_frame(client)
            assert response["ok"] and response["status"]["phase"] == "idle"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as audio:
            audio.connect(str(server.audio_path))
            write_frame(audio, {"op": "authenticate", "token": token, "protocol_version": 3})
            response = read_frame(audio)
            assert response == {"ok": True, "protocol": 3, "sample_rate": 16000}
            payload = bytes([2]) + np.zeros(512, dtype="<f4").tobytes()
            audio.sendall(struct.pack(">I", len(payload)) + payload)
            ready = (
                bytes([1])
                + json.dumps({"event": "ready", "aec": True, "input_sample_rate": 48000}).encode()
            )
            audio.sendall(struct.pack(">I", len(ready)) + ready)
            deadline = time.monotonic() + 1
            while not runtime.native_aec and time.monotonic() < deadline:
                time.sleep(0.01)
            assert runtime.audio_status()["native_audio"]
            assert runtime.audio_status()["aec"]
            done = runtime.native_playback_done[7] = threading.Event()
            drained = bytes([1]) + json.dumps({"event": "drained", "generation": 7}).encode()
            audio.sendall(struct.pack(">I", len(drained)) + drained)
            assert done.wait(1)
            runtime.last_interrupt_requested_ns = time.monotonic_ns()
            stopped = bytes([1]) + json.dumps({"event": "stopped", "generation": 8}).encode()
            audio.sendall(struct.pack(">I", len(stopped)) + stopped)
            deadline = time.monotonic() + 1
            while not runtime.interruption_ms and time.monotonic() < deadline:
                time.sleep(0.01)
            assert runtime.interruption_ms[-1] < 250
        assert not token_path.exists()
        assert server.path.stat().st_mode & 0o077 == 0
        assert server.audio_path.stat().st_mode & 0o077 == 0
    finally:
        server.close()
        runtime.close()

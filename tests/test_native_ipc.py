import os
import secrets
import socket
import struct

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
            write_frame(client, {"op": "authenticate", "token": token})
            assert read_frame(client)["ok"]
            write_frame(client, {"op": "status"})
            response = read_frame(client)
            assert response["ok"] and response["status"]["phase"] == "idle"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as audio:
            audio.connect(str(server.audio_path))
            write_frame(audio, {"op": "authenticate", "token": token})
            response = read_frame(audio)
            assert response == {"ok": True, "protocol": 1, "sample_rate": 16000}
            payload = bytes([2]) + np.zeros(512, dtype="<f4").tobytes()
            audio.sendall(struct.pack(">I", len(payload)) + payload)
        assert not token_path.exists()
        assert server.path.stat().st_mode & 0o077 == 0
        assert server.audio_path.stat().st_mode & 0o077 == 0
    finally:
        server.close()
        runtime.close()

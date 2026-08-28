"""Deadline-bounded IPC operations on private pipes (macOS/POSIX)."""

import os
import select
import time


def read_exact(stream, size: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream.fileno()], [], [], remaining)[0]:
            raise TimeoutError("Local worker response deadline exceeded")
        block = os.read(stream.fileno(), size - len(result))
        if not block:
            raise EOFError("Local worker closed its pipe")
        result.extend(block)
    return bytes(result)


def write_all(stream, packet: bytes, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    view = memoryview(packet)
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [stream.fileno()], [], remaining)[1]:
            raise TimeoutError("Local worker input deadline exceeded")
        try:
            count = os.write(stream.fileno(), view)
        except BlockingIOError:
            continue
        if count <= 0:
            raise BrokenPipeError("Local worker input closed")
        view = view[count:]

"""Ensure process termination reaches service cleanup, including native workers."""

import signal


def termination_cleanup() -> None:
    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)

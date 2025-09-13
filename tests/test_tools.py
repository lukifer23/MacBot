import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot import tools


def test_get_system_info_returns_promptly():
    start = time.perf_counter()
    info = tools.get_system_info()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"get_system_info took too long: {elapsed}s"
    assert info.startswith("System Status:"), info

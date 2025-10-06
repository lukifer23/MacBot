import os
import sys
from types import SimpleNamespace


sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))


def test_get_system_stats_uses_non_blocking_cpu(monkeypatch):
    import macbot.web_dashboard as wd

    cpu_calls = []

    cpu_samples = iter([0.0, 37.2])

    def fake_cpu_percent(interval=None):
        cpu_calls.append(interval)
        try:
            return next(cpu_samples)
        except StopIteration:
            return 37.2

    monkeypatch.setattr(wd.psutil, 'cpu_percent', fake_cpu_percent)
    monkeypatch.setattr(wd.psutil, 'virtual_memory', lambda: SimpleNamespace(percent=55.5))
    monkeypatch.setattr(wd.psutil, 'disk_usage', lambda _: SimpleNamespace(percent=66.6))
    monkeypatch.setattr(wd.psutil, 'net_io_counters', lambda: SimpleNamespace(bytes_sent=123, bytes_recv=456))

    # Force re-prime path to run with our patched cpu_percent implementation
    wd._cpu_percent_initialized = False

    stats = wd.get_system_stats()

    assert stats['cpu'] == 37.2
    assert stats['ram'] == 55.5
    assert stats['disk'] == 66.6
    assert stats['network'] == {'bytes_sent': 123, 'bytes_recv': 456}
    assert all(call is None for call in cpu_calls)
    assert isinstance(stats['timestamp'], str)

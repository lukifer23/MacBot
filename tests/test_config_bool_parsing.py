import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from macbot import config as cfg


def _set_config(monkeypatch: pytest.MonkeyPatch, data: dict) -> None:
    monkeypatch.setattr(cfg, "_CFG", data, raising=False)
    monkeypatch.setattr(cfg, "_LOADED", True, raising=False)


def test_tts_config_boolean_values(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "voice_assistant": {
            "performance": {
                "tts_cache_enabled": False,
                "tts_parallel_processing": True,
                "tts_optimize_for_speed": False,
            }
        }
    }
    _set_config(monkeypatch, data)

    assert cfg.get_tts_cache_enabled() is False
    assert cfg.get_tts_parallel_processing() is True
    assert cfg.get_tts_optimize_for_speed() is False


def test_tts_config_string_boolean_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "voice_assistant": {
            "performance": {
                "tts_cache_enabled": " False ",
                "tts_parallel_processing": "True",
                "tts_optimize_for_speed": "0",
            }
        }
    }
    _set_config(monkeypatch, data)

    assert cfg.get_tts_cache_enabled() is False
    assert cfg.get_tts_parallel_processing() is True
    assert cfg.get_tts_optimize_for_speed() is False


def test_tts_config_invalid_string_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "voice_assistant": {
            "performance": {
                "tts_cache_enabled": "maybe",
            }
        }
    }
    _set_config(monkeypatch, data)

    assert cfg.get_tts_cache_enabled() is True
    assert cfg.get_tts_parallel_processing() is True
    assert cfg.get_tts_optimize_for_speed() is True

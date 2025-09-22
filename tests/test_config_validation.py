import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

import macbot.config as config


def test_validate_config_silent_reports_invalid_config(monkeypatch, tmp_path):
    """validate_config_silent should load the config file before validation."""

    invalid_config_path = tmp_path / "config.yaml"
    invalid_config_path.write_text(
        "models:\n"
        "  llm:\n"
        "    context_length: -5\n"
    )

    original_path = config._CONFIG_PATH
    original_cfg = config._CFG
    original_loaded = config._LOADED

    monkeypatch.setattr(config, "_CONFIG_PATH", str(invalid_config_path), raising=False)

    config._CFG = {}
    config._LOADED = False

    is_valid = None
    warnings = []

    try:
        is_valid, warnings = config.validate_config_silent()
    finally:
        config._CONFIG_PATH = original_path
        config._CFG = original_cfg
        config._LOADED = original_loaded

    assert not is_valid
    assert warnings
    assert any("models.llm.context_length" in warning for warning in warnings)

import pytest
from pydantic import ValidationError

from macbot.config import Settings, load, prepare, save
from macbot.provision import model_dir
from macbot.validation import decode_audio, validate_chat_message


def test_precedence_relative_paths_and_no_tracked_writes(tmp_path):
    config = tmp_path / "explicit.yaml"
    config.write_text("version: 2\ndata_dir: mutable\nmodels:\n  max_tokens: 64\n")
    original = config.read_bytes()
    settings = load(config, {"MACBOT__MODELS__MAX_TOKENS": "96"})
    assert settings.models.max_tokens == 96
    assert settings.data_dir == tmp_path / "mutable"
    prepare(settings)
    save(settings)
    assert config.read_bytes() == original
    assert settings.config_path.stat().st_mode & 0o077 == 0
    other = tmp_path / "override"
    assert load(config, {"MACBOT_DATA_DIR": str(other)}).data_dir == other


def test_missing_explicit_and_legacy_configs_fail(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "absent", {})
    path = tmp_path / "old.yaml"
    path.write_text("models: {}")
    with pytest.raises(ValueError, match="Legacy"):
        load(path, {})


@pytest.mark.parametrize(
    "payload",
    [
        {"services": {"dashboard": {"host": "0.0.0.0", "port": 3000}}},
        {"models": {"llm_url": "https://example.org:8080"}},
        {"models": {"llm_url": "http://secret@127.0.0.1:8080"}},
        {"models": {"llm_url": "http://127.0.0.1:8001"}},
        {"services": {"assistant": {"port": 3000}}},
        {"models": {"max_tokens": 0}},
        {"models": {"stt": "silent-fallback"}},
    ],
)
def test_invalid_config_rejected(payload):
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_inaccessible_or_unregistered_models_never_download(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ValueError):
        model_dir(settings, "/etc/passwd")
    with pytest.raises(FileNotFoundError):
        model_dir(settings, "minilm")
    assert not (tmp_path / "models").exists()


def test_browser_data_url_and_invalid_audio():
    assert decode_audio("data:audio/webm;codecs=opus;base64,YXVkaW8=") == (b"audio", ".webm")
    for invalid in ["data:text/html;base64,SGk=", "%%%", "", None]:
        with pytest.raises(ValueError):
            decode_audio(invalid)
    assert validate_chat_message("  hello <script>  ") == "hello <script>"
    with pytest.raises(ValueError):
        validate_chat_message("x" * 10001)

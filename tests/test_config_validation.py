import pytest
from pydantic import ValidationError

import macbot.provision as provision
from macbot.config import Settings, load, prepare, release_model_manifest, save
from macbot.provision import (
    catalog,
    installed_model_inventory,
    lab_model_manifest,
    model_dir,
    model_roles,
    release_artifacts,
    voice_model,
    voices,
)
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
    "stale",
    [
        "services:\n  browser_fallback_enabled: false\n",
        "models:\n  tts_speed: 1.0\n",
        "tools:\n  approval_seconds: 60\n",
        "tools:\n  auto_run_requested: true\n",
    ],
)
def test_stale_runtime_configuration_is_rejected_instead_of_normalized(tmp_path, stale):
    path = tmp_path / "config.yaml"
    path.write_text("version: 2\n" + stale)
    with pytest.raises(ValidationError):
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
        {"models": {"tts_speed": 1.2}},
        {"models": {"stt": "silent-fallback"}},
        {"models": {"stt": "whisper"}},
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


def test_model_directory_attests_registered_hash_before_use(tmp_path, monkeypatch):
    payload = b"real model bytes"
    import hashlib

    entry = {
        "files": [
            {
                "name": "model.bin",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ]
    }
    monkeypatch.setattr(provision, "catalog", lambda: {"test-model": entry})
    settings = Settings(data_dir=tmp_path)
    target = settings.data_dir / "models/test-model/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    assert provision.model_dir(settings, "test-model") == target.parent
    target.write_bytes(b"fake model bytes")
    with pytest.raises(ValueError, match="checksum mismatch"):
        provision.model_dir(settings, "test-model")


def test_qwen_voice_registry_has_pinned_reproducible_sources_and_outputs():
    entries = catalog()
    assert voices() == ["qwen-aiden-1.7b"]
    item = entries[voice_model(voices()[0])]
    assert len(item["revision"]) == 40
    assert item["repo"].startswith("Qwen/")
    assert item["license"] == "apache-2.0"
    assert item["conversion"] == {
        "type": "mlx_audio_q4",
        "runtime": "mlx-audio",
        "version": "0.5.0",
        "quantization": "4-bit affine",
        "q_bits": 4,
        "q_group_size": 64,
        "model_domain": "tts",
    }
    assert item["source_files"] and item["files"]
    assert all(len(entry["sha256"]) == 64 for entry in item["source_files"])
    assert all(len(entry["sha256"]) == 64 for entry in item["files"])


def test_selected_llm_has_official_reproducible_source_and_quantized_output():
    item = catalog()["qwen3.5-2b-official"]
    assert item["repo"] == "Qwen/Qwen3.5-2B"
    assert item["license"] == "apache-2.0"
    assert len(item["revision"]) == 40
    assert item["conversion"]["type"] == "llama_cpp_q4_k_m"
    assert item["conversion"]["release"] == "b10509"
    assert item["conversion"]["quantization"] == "Q4_K_M"
    assert len(item["source_files"]) >= 10
    assert all(len(entry["sha256"]) == 64 for entry in item["source_files"])
    assert item["files"] == [
        {
            "name": "Qwen3.5-2B-Q4_K_M.gguf",
            "size": 1312164736,
            "sha256": "9a766254d3d0b309b199a39a67e6519c66ab963c40b8564ca6baf40a0f5cf5bf",
        }
    ]


def test_release_manifest_selects_exactly_one_typed_artifact_per_role(tmp_path):
    assert "web_fetch" in Settings().tools.enabled
    manifest = release_model_manifest()
    assert set(manifest) == {"llm", "stt", "tts", "embedding", "vad"}
    assert release_artifacts() == {
        "llm": "qwen3.5-2b-official",
        "stt": "parakeet",
        "tts": "qwen3-tts-1.7b",
        "embedding": "minilm",
        "vad": "silero",
    }
    assert all(entry["release_status"] == "production" for entry in manifest.values())
    assert all(entry["compatibility"]["platform"] == "macOS" for entry in manifest.values())
    lab = lab_model_manifest()
    assert len({entry["artifact"] for entry in lab}) == len(lab)
    assert not set(release_artifacts().values()).intersection(entry["artifact"] for entry in lab)
    assert model_roles("qwen3.5-2b-official") == ("llm",)
    assert model_roles("qwen3.5-2b") == ()
    (tmp_path / "models/qwen3.5-2b-official").mkdir(parents=True)
    (tmp_path / "models/qwen3.5-2b").mkdir()
    inventory = installed_model_inventory(Settings(data_dir=tmp_path))
    assert inventory["extra"] == ["qwen3.5-2b"]
    assert (tmp_path / "models/qwen3.5-2b").is_dir()


def test_browser_data_url_and_invalid_audio():
    assert decode_audio("data:audio/webm;codecs=opus;base64,YXVkaW8=") == (b"audio", ".webm")
    for invalid in ["data:text/html;base64,SGk=", "%%%", "", None]:
        with pytest.raises(ValueError):
            decode_audio(invalid)
    assert validate_chat_message("  hello <script>  ") == "hello <script>"
    with pytest.raises(ValueError):
        validate_chat_message("x" * 10001)

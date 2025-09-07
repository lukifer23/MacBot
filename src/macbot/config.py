"""
Centralized configuration loader and accessors for MacBot.

Loads YAML from `config/config.yaml` and provides typed getters aligned
with the documented schema (models.*, services.*, tools.*, prompts.*, voice_assistant.*).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml


_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml"))
_CFG: Dict[str, Any] = {}
_LOADED = False


def _load() -> None:
    global _CFG, _LOADED
    if _LOADED:
        return
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r") as f:
            _CFG = yaml.safe_load(f) or {}
    else:
        _CFG = {}
    _LOADED = True


def get(path: str, default: Any = None) -> Any:
    """Dot-path getter from loaded config.

    Example: get("models.llm.temperature", 0.4)
    """
    _load()
    cur: Any = _CFG
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def get_llm_server_url() -> str:
    return str(get("models.llm.server_url", "http://localhost:8080/v1/chat/completions"))


def get_llm_temperature() -> float:
    val = get("models.llm.temperature", 0.4)
    try:
        return float(val)
    except Exception:
        return 0.4


def get_llm_max_tokens() -> int:
    val = get("models.llm.max_tokens", 200)
    try:
        return int(val)
    except Exception:
        return 200


def get_system_prompt() -> str:
    return str(get("prompts.system", "You are MacBot, a helpful AI assistant running locally on macOS."))


def get_stt_bin() -> str:
    return os.path.abspath(str(get("models.stt.bin", "models/whisper.cpp/build/bin/whisper-cli")))


def get_stt_model() -> str:
    return os.path.abspath(str(get("models.stt.model", "models/whisper.cpp/models/ggml-base.en.bin")))


def get_stt_language() -> str:
    return str(get("models.stt.language", "en"))


def get_tts_voice() -> str:
    return str(get("models.tts.voice", get("tts.voice", "af_heart")))


def get_tts_speed() -> float:
    val = get("models.tts.speed", get("tts.speed", 1.0))
    try:
        return float(val)
    except Exception:
        return 1.0


def get_audio_sample_rate() -> int:
    try:
        return int(get("voice_assistant.sample_rate", 16000))
    except Exception:
        return 16000


def get_audio_block_sec() -> float:
    try:
        return float(get("voice_assistant.block_sec", 0.03))
    except Exception:
        return 0.03


def get_audio_vad_threshold() -> float:
    try:
        return float(get("voice_assistant.vad_threshold", 0.005))
    except Exception:
        return 0.005


def get_audio_silence_hang() -> float:
    try:
        return float(get("voice_assistant.silence_hang", 0.6))
    except Exception:
        return 0.6


def interruption_enabled() -> bool:
    return bool(get("voice_assistant.interruption.enabled", True))


def get_interrupt_threshold() -> float:
    try:
        return float(get("voice_assistant.interruption.interrupt_threshold", 0.01))
    except Exception:
        return 0.01


def get_interrupt_cooldown() -> float:
    try:
        return float(get("voice_assistant.interruption.interrupt_cooldown", 0.5))
    except Exception:
        return 0.5


def get_conversation_timeout() -> int:
    try:
        return int(get("voice_assistant.interruption.conversation_timeout", 30))
    except Exception:
        return 30


def get_context_buffer_size() -> int:
    try:
        return int(get("voice_assistant.interruption.context_buffer_size", 10))
    except Exception:
        return 10


def get_allowed_apps() -> List[str]:
    return list(get("tools.app_launcher.allowed_apps", []))


def tools_enabled() -> bool:
    """Check if tools are enabled in configuration"""
    enabled_tools = get("tools.enabled", [])
    return len(enabled_tools) > 0


def get_enabled_tools() -> List[str]:
    """Get list of enabled tools"""
    return list(get("tools.enabled", []))


def get_web_dashboard_host_port() -> tuple[str, int]:
    host = str(get("services.web_dashboard.host", "0.0.0.0"))
    try:
        port = int(get("services.web_dashboard.port", 3000))
    except Exception:
        port = 3000
    return host, port


def get_rag_host_port() -> tuple[str, int]:
    host = str(get("services.rag_server.host", "localhost"))
    try:
        port = int(get("services.rag_server.port", 8001))
    except Exception:
        port = 8001
    return host, port


def get_rag_base_url() -> str:
    host, port = get_rag_host_port()
    return f"http://{host}:{port}"


def get_llm_models_endpoint() -> str:
    # Convert chat completions URL to models listing endpoint base
    url = get_llm_server_url()
    # crude base extraction up to host:port
    # expected: http(s)://host:port/v1/chat/completions
    try:
        base = url.split("/v1/")[0]
    except Exception:
        base = "http://localhost:8080"
    return f"{base}/v1/models"


def get_rag_api_tokens() -> List[str]:
    """Return list of allowed RAG API tokens"""
    return list(get("services.rag_server.api_tokens", []))


def get_rag_rate_limit_per_minute() -> int:
    """Return per-token request limit per minute for RAG API"""
    try:
        return int(get("services.rag_server.rate_limit_per_minute", 60))
    except Exception:
        return 60


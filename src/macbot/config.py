"""
Centralized configuration loader and accessors for MacBot.

Loads YAML from `config/config.yaml` and provides typed getters aligned
with the documented schema (models.*, services.*, tools.*, prompts.*, voice_assistant.*).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

import yaml


_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config", "config.yaml"))
_CFG: Dict[str, Any] = {}
_LOADED = False

_BOOL_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on"})
_BOOL_FALSE_VALUES = frozenset({"false", "0", "no", "n", "off"})


def _load() -> None:
    global _CFG, _LOADED
    if _LOADED:
        return
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r") as f:
            _CFG = yaml.safe_load(f) or {}
    else:
        _CFG = {}
    
    # Validate configuration after loading
    _validate_config(_CFG)
    _LOADED = True


def _validate_config(config: Dict[str, Any]) -> None:
    """Validate configuration values and provide helpful error messages"""
    errors = []
    warnings = []
    
    # Validate models section
    if "models" in config:
        models_config = config["models"]
        
        # LLM validation
        if "llm" in models_config:
            llm_config = models_config["llm"]
            
            # Validate model path
            if "path" in llm_config:
                model_path = llm_config["path"]
                if not isinstance(model_path, str):
                    errors.append("models.llm.path must be a string")
                elif not os.path.exists(model_path):
                    warnings.append(f"LLM model file not found: {model_path}")
            
            # Validate context length
            if "context_length" in llm_config:
                ctx_len = llm_config["context_length"]
                if not isinstance(ctx_len, (int, float)) or ctx_len <= 0:
                    errors.append("models.llm.context_length must be a positive number")
                elif ctx_len > 32768:
                    warnings.append("models.llm.context_length is very large, may cause memory issues")
            
            # Validate temperature
            if "temperature" in llm_config:
                temp = llm_config["temperature"]
                if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
                    errors.append("models.llm.temperature must be between 0 and 2")
            
            # Validate max tokens
            if "max_tokens" in llm_config:
                max_tokens = llm_config["max_tokens"]
                if not isinstance(max_tokens, (int, float)) or max_tokens <= 0:
                    errors.append("models.llm.max_tokens must be a positive number")
        
        # STT validation
        if "stt" in models_config:
            stt_config = models_config["stt"]
            
            if "model" in stt_config:
                model_path = stt_config["model"]
                if not isinstance(model_path, str):
                    errors.append("models.stt.model must be a string")
                elif not os.path.exists(model_path):
                    warnings.append(f"STT model file not found: {model_path}")
            
            if "bin" in stt_config:
                bin_path = stt_config["bin"]
                if not isinstance(bin_path, str):
                    errors.append("models.stt.bin must be a string")
                elif not os.path.exists(bin_path):
                    warnings.append(f"STT binary not found: {bin_path}")
        
        # TTS validation
        if "tts" in models_config:
            tts_config = models_config["tts"]
            
            if "piper" in tts_config and "voice_path" in tts_config["piper"]:
                voice_path = tts_config["piper"]["voice_path"]
                if not isinstance(voice_path, str):
                    errors.append("models.tts.piper.voice_path must be a string")
                elif not os.path.exists(voice_path):
                    warnings.append(f"Piper voice model not found: {voice_path}")
    
    # Validate services section
    if "services" in config:
        services_config = config["services"]
        
        # Validate port numbers
        for service_name, service_config in services_config.items():
            if isinstance(service_config, dict) and "port" in service_config:
                port = service_config["port"]
                if not isinstance(port, (int, float)) or port < 1 or port > 65535:
                    errors.append(f"services.{service_name}.port must be between 1 and 65535")
        
        # Validate host addresses
        for service_name, service_config in services_config.items():
            if isinstance(service_config, dict) and "host" in service_config:
                host = service_config["host"]
                if not isinstance(host, str):
                    errors.append(f"services.{service_name}.host must be a string")
                elif not _is_valid_host(host):
                    errors.append(f"services.{service_name}.host is not a valid host address")
    
    # Validate voice assistant section
    if "voice_assistant" in config:
        va_config = config["voice_assistant"]
        
        # Validate audio settings
        if "sample_rate" in va_config:
            sr = va_config["sample_rate"]
            if not isinstance(sr, (int, float)) or sr <= 0:
                errors.append("voice_assistant.sample_rate must be a positive number")
            elif sr not in [8000, 16000, 22050, 44100, 48000]:
                warnings.append("voice_assistant.sample_rate should be a standard rate (8000, 16000, 22050, 44100, 48000)")
        
        # Validate interruption settings
        if "interruption" in va_config:
            int_config = va_config["interruption"]
            
            if "interrupt_threshold" in int_config:
                threshold = int_config["interrupt_threshold"]
                if not isinstance(threshold, (int, float)) or threshold < 0 or threshold > 1:
                    errors.append("voice_assistant.interruption.interrupt_threshold must be between 0 and 1")
            
            if "conversation_timeout" in int_config:
                timeout = int_config["conversation_timeout"]
                if not isinstance(timeout, (int, float)) or timeout <= 0:
                    errors.append("voice_assistant.interruption.conversation_timeout must be a positive number")
    
    # Validate tools section
    if "tools" in config:
        tools_config = config["tools"]
        
        if "enabled" in tools_config:
            enabled_tools = tools_config["enabled"]
            if not isinstance(enabled_tools, list):
                errors.append("tools.enabled must be a list")
            else:
                valid_tools = {"web_search", "screenshot", "app_launcher", "system_monitor", "weather", "rag_search"}
                for tool in enabled_tools:
                    if not isinstance(tool, str):
                        errors.append("tools.enabled items must be strings")
                    elif tool not in valid_tools:
                        warnings.append(f"Unknown tool in tools.enabled: {tool}")
    
    # Validate performance tuning
    try:
        va_cfg = config.get("voice_assistant", {}) or {}
        perf = va_cfg.get("performance", {}) if isinstance(va_cfg, dict) else {}
        if isinstance(perf, dict) and "transcription_cache_window_sec" in perf:
            tw = perf.get("transcription_cache_window_sec")
            if not isinstance(tw, (int, float)) or tw <= 0 or tw > 10:
                errors.append("voice_assistant.performance.transcription_cache_window_sec must be in (0, 10]")
    except Exception:
        pass

    # Report errors and warnings
    if errors:
        error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors)
        raise ValueError(error_msg)
    
    if warnings:
        for warning in warnings:
            print(f"Config warning: {warning}")


def _is_valid_host(host: str) -> bool:
    """Validate host address format"""
    if not host or not isinstance(host, str):
        return False
    
    # Check for localhost variants
    if host in ["localhost", "127.0.0.1", "0.0.0.0"]:
        return True
    
    # Check for valid IP address
    ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    if re.match(ip_pattern, host):
        parts = host.split('.')
        return all(0 <= int(part) <= 255 for part in parts)
    
    # Check for valid hostname
    hostname_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
    return bool(re.match(hostname_pattern, host))


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


def get_typed(path: str, default: Any, cast_type: type) -> Any:
    """Get a configuration value with type casting and default fallback.

    Args:
        path: Dot-separated configuration path
        default: Default value if path not found or casting fails
        cast_type: Type to cast the value to

    Returns:
        The cast value or default
    """
    val = get(path, default)

    if cast_type is bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            normalized = val.strip().lower()
            if normalized in _BOOL_TRUE_VALUES:
                return True
            if normalized in _BOOL_FALSE_VALUES:
                return False
            return default
        try:
            return bool(val)
        except (TypeError, ValueError):
            return default

    if isinstance(val, cast_type):
        return val

    try:
        return cast_type(val)
    except (TypeError, ValueError):
        return default


def get_llm_server_url() -> str:
    return str(get("models.llm.server_url", "http://localhost:8080/v1/chat/completions"))

def get_llm_model_path() -> str:
    return os.path.abspath(str(get("models.llm.path", "models/llama.cpp/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf")))

def get_llm_context_length() -> int:
    return get_typed("models.llm.context_length", 4096, int)

def get_llm_threads() -> int:
    return get_typed("models.llm.threads", -1, int)

def get_llm_temperature() -> float:
    return get_typed("models.llm.temperature", 0.4, float)

def get_llm_max_tokens() -> int:
    return get_typed("models.llm.max_tokens", 200, int)


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
    return get_typed("models.tts.speed", get("tts.speed", 1.0), float)

def get_piper_voice_path() -> str:
    """Filesystem path to Piper ONNX voice model.
    Default points to en_US lessac medium if present in repo structure.
    """
    return os.path.abspath(str(get("models.tts.piper.voice_path", "piper_voices/en_US-lessac-medium/model.onnx")))

def get_piper_sample_rate() -> int:
    return get_typed("models.tts.piper.sample_rate", 22050, int)

def get_piper_reload_sec() -> int:
    return get_typed("models.tts.piper.reload_sec", 30, int)

def get_audio_sample_rate() -> int:
    return get_typed("voice_assistant.sample_rate", 16000, int)

def get_audio_block_sec() -> float:
    return get_typed("voice_assistant.block_sec", 0.03, float)

def get_audio_vad_threshold() -> float:
    return get_typed("voice_assistant.vad_threshold", 0.005, float)

def get_audio_silence_hang() -> float:
    return get_typed("voice_assistant.silence_hang", 0.6, float)


# Performance optimization settings
def get_transcription_cache_size() -> int:
    """Get transcription cache size"""
    return get_typed("voice_assistant.performance.transcription_cache_size", 10, int)

def get_min_chunk_duration() -> float:
    """Get minimum chunk duration for transcription"""
    return get_typed("voice_assistant.performance.min_chunk_duration", 0.5, float)

def get_transcription_interval() -> float:
    """Get minimum interval between transcriptions"""
    return get_typed("voice_assistant.performance.transcription_interval", 0.3, float)

def get_transcription_cache_window_sec() -> float:
    """Window size (seconds) used to key streaming transcription cache."""
    return get_typed("voice_assistant.performance.transcription_cache_window_sec", 2.0, float)

def get_tts_buffer_size() -> int:
    """Get TTS buffer size for streaming"""
    return get_typed("voice_assistant.performance.tts_buffer_size", 180, int)

def get_tts_cache_size() -> int:
    """Get TTS cache size"""
    return get_typed("voice_assistant.performance.tts_cache_size", 100, int)

def get_tts_cache_enabled() -> bool:
    """Get TTS cache enabled status"""
    return get_typed("voice_assistant.performance.tts_cache_enabled", True, bool)

def get_tts_parallel_processing() -> bool:
    """Get TTS parallel processing enabled status"""
    return get_typed("voice_assistant.performance.tts_parallel_processing", True, bool)

def get_tts_optimize_for_speed() -> bool:
    """Get TTS optimize for speed status"""
    return get_typed("voice_assistant.performance.tts_optimize_for_speed", True, bool)

def get_piper_quantized_path() -> Optional[str]:
    """Get quantized Piper model path"""
    path = get("models.tts.piper.quantized_path")
    return path if path and os.path.exists(path) else None

def get_piper_coreml_path() -> Optional[str]:
    """Get CoreML Piper model path"""
    path = get("models.tts.piper.coreml_path")
    return path if path and os.path.exists(path) else None

def get_piper_fallback_path() -> Optional[str]:
    """Get fallback Piper model path"""
    path = get("models.tts.piper.fallback_path")
    return path if path and os.path.exists(path) else None


def interruption_enabled() -> bool:
    return bool(get("voice_assistant.interruption.enabled", True))


def get_interrupt_threshold() -> float:
    return get_typed("voice_assistant.interruption.interrupt_threshold", 0.01, float)

def get_audio_output_device():
    """Return configured output device (int index or str name) or None."""
    return get("voice_assistant.audio.output_device", None)

def get_audio_input_device():
    """Return configured input device (int index or str name) or None."""
    return get("voice_assistant.audio.input_device", None)

def mic_mute_while_tts() -> bool:
    """When true, ignore mic input while assistant is speaking to avoid feedback loops."""
    return get_typed("voice_assistant.audio.mic_mute_while_tts", True, bool)

def get_interrupt_cooldown() -> float:
    return get_typed("voice_assistant.interruption.interrupt_cooldown", 0.5, float)

def get_conversation_timeout() -> int:
    return get_typed("voice_assistant.interruption.conversation_timeout", 30, int)

def get_context_buffer_size() -> int:
    return get_typed("voice_assistant.interruption.context_buffer_size", 10, int)


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

def get_voice_assistant_host_port() -> tuple[str, int]:
    """Host/port for the voice assistant control server"""
    host = str(get("services.voice_assistant.host", "localhost"))
    try:
        port = int(get("services.voice_assistant.port", 8123))
    except Exception:
        port = 8123
    return host, port


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


def get_llm_chat_endpoint() -> str:
    """Return the LLM server's /v1/chat/completions endpoint URL"""
    url = get_llm_server_url()
    # expected: http(s)://host:port/v1/chat/completions
    try:
        base = url.split("/v1/")[0]
    except Exception:
        base = "http://localhost:8080"
    return f"{base}/v1/chat/completions"


def get_rag_api_tokens() -> List[str]:
    """Return list of allowed RAG API tokens"""
    # Check environment variable first for security
    env_tokens = os.getenv("MACBOT_RAG_API_TOKENS", "")
    if env_tokens:
        return env_tokens.split(",")

    # Fallback to config file (deprecated)
    tokens = get("services.rag_server.api_tokens", [])
    if not tokens:
        tokens = get("services.rag.api_tokens", [])
    return list(tokens) if tokens else []


def get_rag_rate_limit_per_minute() -> int:
    """Return per-token request limit per minute for RAG API"""
    return get_typed("services.rag_server.rate_limit_per_minute", 60, int)

def get_orchestrator_host_port() -> tuple[str, int]:
    """Host/port for the orchestrator control server"""
    host = str(get("services.orchestrator.host", "localhost"))
    port = get_typed("services.orchestrator.port", 8090, int)
    return host, port


def validate_config_silent() -> tuple[bool, List[str]]:
    """Validate configuration without raising exceptions

    Returns:
        tuple: (is_valid, list_of_warnings)
    """
    try:
        if not _LOADED:
            _load()
        _validate_config(_CFG)
        return True, []
    except ValueError as e:
        return False, [str(e)]
    except Exception as e:
        return False, [f"Validation error: {e}"]


def get_all() -> Dict[str, Any]:
    """Get the entire configuration dictionary"""
    _load()
    return _CFG.copy()


def reload_config() -> None:
    """Reload configuration from file"""
    global _CFG, _LOADED
    _LOADED = False
    _CFG = {}
    _load()

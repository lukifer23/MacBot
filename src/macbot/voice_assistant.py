import os
import queue
import subprocess
import json
import tempfile
import time
import threading
import sys
import signal
import uuid
import numpy as np
import psutil
import platform
try:
    import sounddevice as sd
except Exception as _sd_imp_err:
    sd = None  # type: ignore
try:
    import soundfile as sf
except Exception as _sf_imp_err:
    sf = None  # type: ignore
import requests
import logging
from .logging_utils import setup_logger
from typing import Dict, List, Optional, Any
from pathlib import Path

from .audio_interrupt import AudioInterruptHandler
from .conversation_manager import (
    ConversationManager,
    ConversationContext,
    ConversationState,
    ResponseState,
)
from .message_bus_client import MessageBusClient
from . import config as CFG
from . import tools
from .validation import validate_chat_message
from .resource_manager import get_resource_manager, managed_temp_file, managed_resource, track_resource

# Configure logging with structured logging
logger = setup_logger("macbot.voice_assistant", "logs/voice_assistant.log", structured=True)

# Dashboard notifications
_WD_HOST, _WD_PORT = CFG.get_web_dashboard_host_port()
VA_HOST, VA_PORT = CFG.get_voice_assistant_host_port()

def _notify_dashboard_state(event_type: str, message: str = "") -> None:
    """Non-blocking notify with small retry/backoff to improve reliability."""
    url = f"http://{_WD_HOST}:{_WD_PORT}/api/assistant-event"
    payload = {"type": event_type}
    if message:
        payload["message"] = message

    def _send():
        delay = 0.15
        for _ in range(3):
            try:
                requests.post(url, json=payload, timeout=0.8)
                return
            except Exception as e:
                try:
                    time.sleep(delay)
                except Exception:
                    pass
                delay *= 2
        logger.debug("Dashboard notify dropped after retries")

    try:
        threading.Thread(target=_send, daemon=True).start()
    except Exception as e:
        logger.debug(f"Notify thread failed: {e}")

# No heavy optional deps needed here; RAG is handled via HTTP client.

LLAMA_SERVER = CFG.get_llm_server_url()
LLAMA_TEMP   = CFG.get_llm_temperature()
LLAMA_MAXTOK = CFG.get_llm_max_tokens()

SYSTEM_PROMPT = CFG.get_system_prompt()

WHISPER_BIN   = CFG.get_stt_bin()
WHISPER_MODEL = CFG.get_stt_model()
WHISPER_LANG  = CFG.get_stt_language()

VOICE    = CFG.get_tts_voice()
SPEED    = CFG.get_tts_speed()

SAMPLE_RATE   = CFG.get_audio_sample_rate()
BLOCK_DUR     = CFG.get_audio_block_sec()
VAD_THRESH    = CFG.get_audio_vad_threshold()
SILENCE_HANG  = CFG.get_audio_silence_hang()

# Interruption settings
INTERRUPTION_ENABLED = CFG.interruption_enabled()
INTERRUPT_THRESHOLD = CFG.get_interrupt_threshold()
INTERRUPT_COOLDOWN = CFG.get_interrupt_cooldown()
CONVERSATION_TIMEOUT = CFG.get_conversation_timeout()
CONTEXT_BUFFER_SIZE = CFG.get_context_buffer_size()

# Constants
MAX_INPUT_LENGTH = 2000  # Maximum input length for safety
LLM_TIMEOUT = 120  # LLM request timeout in seconds
HEALTH_CHECK_TIMEOUT = 2  # Health check timeout in seconds
TURNING_DELAY = 0.35  # Delay for turn detection in seconds
TTS_SAMPLE_RATE = 24000  # TTS audio sample rate
TTS_RATE_MULTIPLIER = 180  # TTS rate multiplier for pyttsx3

# Performance optimization constants
MAX_CONCURRENT_TTS = 2  # Maximum concurrent TTS operations
TTS_QUEUE_TIMEOUT = 5.0  # TTS queue timeout in seconds
PERFORMANCE_LOG_INTERVAL = 10  # Log performance every N requests

# Optional Python bindings for whisper.cpp / whisper
try:
    import whispercpp

    try:
        _WHISPER_CTX = whispercpp.Whisper.from_pretrained("base.en")
        _WHISPER_IMPL = "whispercpp"
    except Exception as _werr:  # pragma: no cover - best effort
        logger.warning(f"Failed to init whispercpp: {_werr}")
        _WHISPER_CTX = None
        _WHISPER_IMPL = "cli"
except Exception as _e:  # pragma: no cover - best effort
    logger.warning(f"whispercpp not available: {_e}")
    try:
        import whisper as _openai_whisper  # type: ignore

        try:
            _WHISPER_CTX = _openai_whisper.load_model("base")
            _WHISPER_IMPL = "whisper"
        except Exception as _owerr:  # pragma: no cover - best effort
            logger.warning(f"Failed to load openai whisper: {_owerr}")
            _WHISPER_CTX = None
            _WHISPER_IMPL = "cli"
    except Exception as _ie:  # pragma: no cover
        logger.warning(f"whisper library not available: {_ie}")
        _WHISPER_CTX = None
        _WHISPER_IMPL = "cli"

# ---- Optional: LiveKit turn detector ----
try:
    from livekit.plugins.turn_detector.english import EnglishModel
    TURN_DETECT = EnglishModel()
    HAS_TURN_DETECT = True
except Exception as e:
    print(f"[warn] turn-detector unavailable ({e}); falling back to VAD-only endpointing")
    HAS_TURN_DETECT = False

# ---- Tool calling system ----
class ToolCaller:
    """Dispatch helper that only exposes tools permitted by configuration."""

    _METHOD_MAP = {
        "web_search": ("web_search", tools.web_search),
        "browse_website": ("web_search", tools.browse_website),
        "get_system_info": ("system_monitor", tools.get_system_info),
        "search_knowledge_base": ("rag_search", lambda q: tools.rag_search(q)),
        "open_app": ("app_launcher", tools.open_app),
        "take_screenshot": ("screenshot", tools.take_screenshot),
        "get_weather": ("weather", lambda: tools.get_weather()),
    }

    def __init__(self):
        self._enabled_tools = set(CFG.get_enabled_tools())
        self._method_to_feature = {
            method: feature for method, (feature, _) in self._METHOD_MAP.items()
        }
        self.tools = {
            method: func
            for method, (feature, func) in self._METHOD_MAP.items()
            if feature in self._enabled_tools
        }

    def has_enabled_tools(self) -> bool:
        return bool(self._enabled_tools)

    def is_tool_enabled(self, tool_name: str) -> bool:
        return tool_name in self._enabled_tools

    def _get_callable(self, method_name: str):
        func = self.tools.get(method_name)
        if func is None:
            logger.debug(f"Tool callable '{method_name}' not available - check configuration")
        return func

    def _execute_tool(self, tool_name: str, method_name: str, *args, unavailable_msg: str = None, **kwargs) -> str:
        """Generic tool execution with error handling"""
        if not self.is_tool_enabled(tool_name):
            return f"{tool_name.replace('_', ' ').title()} is currently disabled."

        func = self._get_callable(method_name)
        if func is None:
            return unavailable_msg or f"I couldn't perform this action right now. The {tool_name.replace('_', ' ')} service might be unavailable."

        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"{tool_name} failed: {e}")
            return unavailable_msg or f"I couldn't perform this action right now. The {tool_name.replace('_', ' ')} service might be unavailable."

    def web_search(self, query: str) -> str:
        return self._execute_tool(
            "web_search", "web_search", query,
            unavailable_msg=f"I couldn't perform a web search for '{query}' right now. The web search service might be unavailable."
        )

    def browse_website(self, url: str) -> str:
        return self._execute_tool(
            "web_search", "browse_website", url,
            unavailable_msg=f"I couldn't open {url} right now. The website browsing service might be unavailable."
        )

    def get_system_info(self) -> str:
        return self._execute_tool(
            "system_monitor", "get_system_info",
            unavailable_msg="I couldn't retrieve system information right now. The system monitoring service might be unavailable."
        )

    def search_knowledge_base(self, query: str) -> str:
        return self._execute_tool(
            "rag_search", "search_knowledge_base", query,
            unavailable_msg=f"I couldn't search the knowledge base for '{query}' right now. The RAG service might be unavailable."
        )

    def open_app(self, app_name: str) -> str:
        return self._execute_tool(
            "app_launcher", "open_app", app_name,
            unavailable_msg=f"I couldn't open {app_name} right now. The application launcher service might be unavailable."
        )

    def take_screenshot(self) -> str:
        return self._execute_tool(
            "screenshot", "take_screenshot",
            unavailable_msg="I couldn't take a screenshot right now. The screenshot service might be unavailable."
        )

    def get_weather(self) -> str:
        return self._execute_tool(
            "weather", "get_weather",
            unavailable_msg="I couldn't get weather information right now. The weather service might be unavailable."
        )

# Initialize tool caller
tool_caller = ToolCaller()

# RAG handled via external rag_server (see macbot.tools.rag_search)

# ---- Simple energy VAD ----
def is_voiced(block: np.ndarray, thresh: float = VAD_THRESH) -> bool:
    # Optimize: handle multi-dimensional arrays without explicit reshape
    # Use ravel() for a view instead of flatten() for a copy
    return np.sqrt(np.mean(block.ravel()**2)) > thresh

def check_llm_service_available() -> bool:
    """Check if LLM service is available"""
    try:
        response = requests.get(LLAMA_SERVER.replace("/v1/chat/completions", "/health"), timeout=HEALTH_CHECK_TIMEOUT)
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"LLM service health check failed: {e}")
        return False

def validate_input(text: str, max_length: int = MAX_INPUT_LENGTH) -> bool:
    """Validate user input to prevent issues"""
    try:
        validate_chat_message(text)
        return True
    except Exception as e:
        logger.warning(f"Input validation failed: {e}")
        return False

# ---- Audio I/O ----
audio_q = queue.Queue()

def _callback(indata: np.ndarray, frames: int, time_info, status) -> None:
    if status:
        print(status, file=sys.stderr)
    
    # Fast path: check if we should process audio at all
    try:
        if CFG.mic_mute_while_tts() and tts_manager and tts_manager.audio_handler and getattr(tts_manager.audio_handler, 'is_playing', False):
            return
    except Exception:
        pass
    
    # Put audio data in queue for processing
    audio_q.put(indata)

    # Optimized interruptibility check - minimize lock time
    if INTERRUPTION_ENABLED and tts_manager.audio_handler and conversation_manager:
        # Quick state check without holding lock
        current_context = conversation_manager.current_context
        if (current_context and 
            current_context.current_state == ConversationState.SPEAKING and
            tts_manager.audio_handler.check_voice_activity(indata.flatten())):
            
            # Only acquire lock for the actual interrupt
            conversation_manager.interrupt_response()

# ---- Whisper transcription ----
def _transcribe_cli(wav_f32: np.ndarray) -> str:
    """Fallback transcription via whisper.cpp CLI using temp files."""
    try:
        with managed_temp_file(suffix=".wav") as tmp_file:
            txt_path = f"{tmp_file.name}.txt"
            try:
                if sf is not None:
                    sf.write(tmp_file.name, wav_f32, SAMPLE_RATE, subtype="PCM_16")
                else:
                    import wave
                    with wave.open(tmp_file.name, 'wb') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)  # 16-bit
                        wf.setframerate(SAMPLE_RATE)
                        # clip and convert to int16
                        data_i16 = np.clip(wav_f32, -1.0, 1.0)
                        data_i16 = (data_i16 * 32767.0).astype(np.int16)
                        wf.writeframes(data_i16.tobytes())
            except Exception as e:
                logger.error(f"Failed to write WAV: {e}")
                return ""

            try:
                # call whisper.cpp
                # -nt = no timestamps, -l language
                # -otxt = output to text file
                cmd = [
                    WHISPER_BIN,
                    "-m",
                    WHISPER_MODEL,
                    "-f",
                    tmp_file.name,
                    "-l",
                    WHISPER_LANG,
                    "-nt",
                    "-otxt",
                    "-of",
                    tmp_file.name,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                logger.error("Whisper transcription timed out")
                return ""

            try:
                if proc.returncode != 0:
                    logger.error(f"Whisper transcription failed: {proc.stderr}")
                    return ""
                if not os.path.exists(txt_path):
                    logger.error("Whisper output file not found")
                    return ""
                with open(txt_path, "r") as rf:
                    text = rf.read().strip()
                return text
            finally:
                try:
                    if os.path.exists(txt_path):
                        os.unlink(txt_path)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to cleanup Whisper output file {txt_path}: {cleanup_err}")
    except Exception as e:  # pragma: no cover
        logger.error(f"Transcription error: {e}")
        return ""


def transcribe(wav_f32: np.ndarray) -> str:
    """Transcribe audio using in-memory pipeline with graceful degradation."""
    if _WHISPER_IMPL == "whispercpp" and _WHISPER_CTX is not None:
        try:
            result = _WHISPER_CTX.transcribe(wav_f32)
            return str(result).strip()
        except Exception as e:  # pragma: no cover
            logger.error(f"whispercpp transcription failed: {e}")
    elif _WHISPER_IMPL == "whisper" and _WHISPER_CTX is not None:
        try:
            result = _WHISPER_CTX.transcribe(wav_f32)  # type: ignore
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            return str(text).strip()
        except Exception as e:  # pragma: no cover
            logger.error(f"whisper transcription failed: {e}")
    # Fallback to CLI
    return _transcribe_cli(wav_f32)


class StreamingTranscriber:
    """Optimized streaming transcriber with intelligent buffering and segment tracking."""

    def __init__(self, max_buffer_duration: float = 30.0, sample_rate: int = 16000) -> None:
        self._buffer: np.ndarray = np.array([], dtype=np.float32)
        self._buffer_offset: int = 0  # Absolute sample index of the first element in ``_buffer``
        self._processed_offset: int = 0  # Absolute index up to which audio has been transcribed
        self._next_sample_index: int = 0  # Absolute index assigned to the next appended sample

        self._last_text: str = ""
        self._segments: List[Dict[str, Any]] = []

        self._max_buffer_samples = int(max_buffer_duration * sample_rate)
        self._sample_rate = sample_rate
        self._min_chunk_size = int(CFG.get_min_chunk_duration() * sample_rate)

        cwin_sec = CFG.get_transcription_cache_window_sec()
        window_samples = int(max(1.0, cwin_sec) * sample_rate)
        self._window_samples = max(self._min_chunk_size, window_samples)
        self._lookback_samples = min(self._window_samples, self._max_buffer_samples)

        # Performance tracking
        self._transcription_count = 0
        self._last_transcription_time = 0.0
        self._transcription_interval = CFG.get_transcription_interval()

    def add_chunk(self, chunk: np.ndarray) -> str:
        if chunk.size == 0:
            return ""

        # Append new audio and update absolute indices
        self._buffer = np.concatenate([self._buffer, chunk])
        self._next_sample_index += len(chunk)

        delta_fragments: List[str] = []
        aggregated_text = self._last_text
        processed_any = False

        while True:
            pending = self._next_sample_index - self._processed_offset
            if pending < self._min_chunk_size:
                break

            current_time = time.time()
            if (not processed_any and
                current_time - self._last_transcription_time <= self._transcription_interval):
                break

            chunk_length = min(pending, self._window_samples)

            start_index = self._processed_offset - self._buffer_offset
            end_index = start_index + chunk_length
            if start_index < 0:
                # Should not happen, but guard against race conditions with trimming
                start_index = 0
                end_index = min(len(self._buffer), chunk_length)

            if end_index > len(self._buffer):
                end_index = len(self._buffer)
                chunk_length = end_index - start_index

            if chunk_length <= 0:
                break

            audio_window = self._buffer[start_index:end_index]
            text = transcribe(audio_window)
            normalized_text = text.strip()

            segment_start = self._processed_offset
            segment_end = segment_start + chunk_length
            self._segments.append(
                {
                    "start": segment_start,
                    "end": segment_end,
                    "text": normalized_text,
                    "timestamp": current_time,
                }
            )

            self._processed_offset = segment_end
            self._transcription_count += 1
            self._last_transcription_time = current_time
            processed_any = True

            previous_text = aggregated_text
            if normalized_text:
                aggregated_text = (f"{aggregated_text} {normalized_text}" if aggregated_text else normalized_text).strip()
            delta_part = aggregated_text[len(previous_text):]
            if delta_part:
                delta_fragments.append(delta_part)

        if processed_any:
            self._last_text = aggregated_text
            delta_text = "".join(delta_fragments).strip()
        else:
            delta_text = ""

        self._trim_buffer()
        return delta_text

    def flush(self) -> str:
        text = self._last_text.strip()
        self._buffer = np.array([], dtype=np.float32)
        self._buffer_offset = 0
        self._processed_offset = 0
        self._next_sample_index = 0
        self._segments.clear()
        self._last_text = ""
        return text

    def _trim_buffer(self) -> None:
        """Trim processed audio while keeping a short lookback window."""

        # Determine the earliest sample we need to retain for lookback
        target_offset = max(0, self._processed_offset - self._lookback_samples)
        target_offset = max(target_offset, self._next_sample_index - self._max_buffer_samples)

        if target_offset <= self._buffer_offset:
            return

        drop = target_offset - self._buffer_offset
        if drop >= len(self._buffer):
            self._buffer = np.array([], dtype=np.float32)
        else:
            self._buffer = self._buffer[drop:]

        self._buffer_offset = target_offset

# ---- Enhanced LLM chat with tool calling ----
TTS_STREAMED = False  # set true when llama_chat performs streaming TTS

def llama_chat(user_text: str) -> str:
    # Check if user is requesting tool usage
    tool_support = tool_caller if tool_caller and tool_caller.has_enabled_tools() else None
    if tool_support:
        # Enhanced keyword-based tool detection
        user_text_lower = user_text.lower()

        # Web search
        if (
            tool_support.is_tool_enabled("web_search")
            and "search" in user_text_lower
            and ("web" in user_text_lower or "for" in user_text_lower)
        ):
            query = user_text_lower.replace("search", "").replace("for", "").replace("web", "").strip()
            result = tool_support.web_search(query)
            return f"I searched for '{query}'. {result}"

        # Website browsing
        elif (
            tool_support.is_tool_enabled("web_search")
            and (
                "browse" in user_text_lower
                or "website" in user_text_lower
                or "open website" in user_text_lower
            )
        ):
            words = user_text.split()
            for word in words:
                if word.startswith(("http://", "https://", "www.")):
                    result = tool_support.browse_website(word)
                    return f"I browsed {word}. {result}"

        # App opening
        elif (
            tool_support.is_tool_enabled("app_launcher")
            and "open" in user_text_lower
            and "app" in user_text_lower
        ):
            app_name = user_text_lower.replace("open", "").replace("app", "").strip()
            result = tool_support.open_app(app_name)
            return result

        # Screenshot
        elif tool_support.is_tool_enabled("screenshot") and (
            "screenshot" in user_text_lower or "take picture" in user_text_lower
        ):
            result = tool_support.take_screenshot()
            return result

        # Weather
        elif tool_support.is_tool_enabled("weather") and "weather" in user_text_lower:
            result = tool_support.get_weather()
            return result

        # System info
        elif (
            tool_support.is_tool_enabled("system_monitor")
            and "system" in user_text_lower
            and "info" in user_text_lower
        ):
            result = tool_support.get_system_info()
            return f"Here's your system information: {result}"

        # RAG search
        elif tool_support.is_tool_enabled("rag_search") and any(
            keyword in user_text_lower
            for keyword in ["knowledge", "document", "file", "kb", "search kb", "search knowledge"]
        ):
            result = tool_support.search_knowledge_base(user_text)
            return result
    
    # Regular chat if no tools needed
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ],
        "temperature": LLAMA_TEMP,
        "max_tokens": LLAMA_MAXTOK,
        "stream": True
    }

    try:
        global TTS_STREAMED
        TTS_STREAMED = False
        # Inform UI that assistant will start speaking (streamed)
        _notify_dashboard_state('speaking_started')
        with requests.post(
            LLAMA_SERVER,
            json=payload,
            stream=True,
            timeout=LLM_TIMEOUT,
        ) as r:
            r.raise_for_status()

            full_response = ""
            tts_buf = ""

            if INTERRUPTION_ENABLED and conversation_manager:
                conversation_manager.start_response()

            pending_tts_jobs: List['TTSJob'] = []

            for line in r.iter_lines(decode_unicode=True):
                if INTERRUPTION_ENABLED and tts_manager.audio_handler and tts_manager.audio_handler.interrupt_requested:
                    if conversation_manager:
                        conversation_manager.interrupt_response()
                    break

                if not line:
                    continue

                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content", "")
                    except Exception:
                        delta = ""

                    if not delta:
                        continue

                    full_response += delta

                    if INTERRUPTION_ENABLED and conversation_manager:
                        conversation_manager.update_response(full_response)

                    # Optimized TTS streaming with better sentence boundary detection
                    tts_buf += delta
                    
                    # More intelligent sentence boundary detection
                    sentence_endings = [".", "?", "!", "\n", ":", ";"]
                    tts_buffer_size = CFG.get_tts_buffer_size()
                    flush_now = (any(p in delta for p in sentence_endings) or 
                               len(tts_buf) > tts_buffer_size or
                               (len(tts_buf) > 100 and any(p in tts_buf for p in [",", "and", "but", "so"])))
                    
                    if flush_now and tts_buf.strip():
                        to_say = tts_buf.strip()
                        tts_buf = ""
                        TTS_STREAMED = True

                        # Check if we should still speak (not interrupted)
                        if (INTERRUPTION_ENABLED and conversation_manager and
                            conversation_manager.current_context and
                            conversation_manager.current_context.response_state != ResponseState.INTERRUPTED):
                            job = tts_manager.enqueue_speak(to_say, interruptible=True, notify=False)
                            pending_tts_jobs.append(job)

            if tts_buf.strip():
                TTS_STREAMED = True
                job = tts_manager.enqueue_speak(tts_buf.strip(), interruptible=True, notify=False)
                pending_tts_jobs.append(job)

            if INTERRUPTION_ENABLED and conversation_manager and not (tts_manager.audio_handler and tts_manager.audio_handler.interrupt_requested):
                conversation_manager.update_response(full_response, is_complete=True)
                try:
                    conversation_manager.complete_response()
                except Exception:
                    pass

            for job in pending_tts_jobs:
                try:
                    job.wait()
                except Exception:
                    pass

            # Signal end/interrupted after last chunk queued
            try:
                if tts_manager.audio_handler and tts_manager.audio_handler.interrupt_requested:
                    _notify_dashboard_state('speaking_interrupted')
                else:
                    _notify_dashboard_state('speaking_ended')
            except Exception:
                _notify_dashboard_state('speaking_ended')
            return full_response
    except requests.exceptions.Timeout:
        return "The language model is taking too long to respond. Please try again."
    except requests.exceptions.ConnectionError:
        return "I can't connect to the language model right now. Please check if the LLM server is running."
    except Exception as e:
        logger.error(f"LLM processing error: {e}")
        return f"I'm having trouble connecting to the language model: {str(e)}"

def get_degraded_response(user_text: str) -> str:
    """Provide basic responses when services are unavailable"""
    user_text_lower = user_text.lower()

    # Basic command recognition without external services
    if any(word in user_text_lower for word in ["hello", "hi", "hey"]):
        return "Hello! I'm MacBot, but some of my services aren't available right now."

    elif "time" in user_text_lower:
        current_time = time.strftime("%I:%M %p")
        return f"The current time is {current_time}."

    elif "date" in user_text_lower:
        current_date = time.strftime("%A, %B %d, %Y")
        return f"Today is {current_date}."

    elif any(word in user_text_lower for word in ["help", "what can you do"]):
        return ("I can help with basic tasks, but some services are currently unavailable. "
                "Try asking for the time, date, or basic information.")

    elif any(word in user_text_lower for word in ["status", "system", "info"]):
        return "System monitoring is currently unavailable, but I'm still here to help with basic questions."

    else:
        return ("I'm sorry, but some of my services are currently unavailable. "
                "I can still help with basic questions about time, date, or general assistance.")

# ---- TTS Setup ----
# Unified TTS system with proper fallback handling
class TTSJob:
    """Container for queued TTS work with completion helpers."""

    def __init__(self, text: str, interruptible: bool, notify: bool):
        self.text = text
        self.interruptible = interruptible
        self.notify = notify
        self.done_event = threading.Event()
        self.success: bool = False
        self.error: Optional[Exception] = None

    def set_result(self, success: bool, error: Optional[Exception] = None) -> None:
        self.success = success
        self.error = error
        self.done_event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        finished = self.done_event.wait(timeout)
        if not finished:
            return False
        return self.success

    def done(self) -> bool:
        return self.done_event.is_set()


class TTSManager:
    """Unified TTS manager handling different engines and interruption"""

    def __init__(self):
        self.engine = None
        self.engine_type = None
        self.audio_handler = None
        self.voices = []  # available voice names/ids for the active engine
        self.kokoro_available = False

        # Performance monitoring
        self.performance_lock = threading.Lock()
        self.performance_stats = self._default_performance_stats()
        self.pyttsx3_available = False
        self.piper_available = False
        self.say_available = False
        self._speak_lock = threading.Lock()
        self._initialized = False  # lazy init to avoid crashes on import

        # TTS queue system for resource management
        self._tts_queue: "queue.Queue[Optional[TTSJob]]" = queue.Queue()
        self._active_tts_count = 0
        self._tts_count_lock = threading.Lock()
        self._tts_workers: List[threading.Thread] = []
        self._tts_shutdown = threading.Event()

        # Async TTS processing
        self._tts_cache = {}  # Cache for common phrases
        self._cache_max_size = CFG.get_tts_cache_size()
        self._cache_enabled = CFG.get_tts_cache_enabled()
        self._parallel_processing = CFG.get_tts_parallel_processing()
        self._optimize_for_speed = CFG.get_tts_optimize_for_speed()
        self._cache_lock = threading.Lock()

        # Hardware acceleration detection
        self._mps_available = self._detect_mps_support()
        self._coreml_available = self._detect_coreml_support()
        logger.info(f"🚀 Hardware acceleration: MPS={self._mps_available}, CoreML={self._coreml_available}")
        self._reload_sec = CFG.get_piper_reload_sec()

        if os.environ.get("MACBOT_DISABLE_TTS") == "1":
            print("⚠️ TTS disabled via environment variable")
            self._tts_shutdown.set()
            return

        self._start_tts_workers()

        # Only Piper is supported now; no other engines to probe
        self.say_available = False

    def _default_performance_stats(self) -> Dict[str, Any]:
        return {
            'total_requests': 0,
            'total_duration': 0.0,
            'avg_duration': 0.0,
            'max_duration': 0.0,
            'min_duration': float('inf'),
            'errors': 0,
            'last_request_time': 0.0,
            'active_jobs': 0,
            'queued_jobs': 0,
        }

    def _start_tts_workers(self) -> None:
        """Start the fixed worker pool responsible for handling queued jobs."""
        if self._tts_workers:
            return

        for idx in range(MAX_CONCURRENT_TTS):
            worker = threading.Thread(
                target=self._tts_worker_loop,
                name=f"TTSWorker-{idx}",
                daemon=True,
            )
            worker.start()
            self._tts_workers.append(worker)

    def _tts_worker_loop(self) -> None:
        """Continuously process queued TTS jobs respecting concurrency limits."""
        while True:
            try:
                job = self._tts_queue.get(timeout=0.2)
            except queue.Empty:
                if self._tts_shutdown.is_set():
                    break
                continue

            if job is None:
                self._tts_queue.task_done()
                break

            if isinstance(job, TTSJob):
                self._execute_job(job)
                self._tts_queue.task_done()
            else:
                # Unexpected payload, just acknowledge to avoid deadlock
                self._tts_queue.task_done()

    def _execute_job(self, job: TTSJob) -> None:
        """Execute a queued TTS job, handling lifecycle metrics."""
        with self._tts_count_lock:
            self._active_tts_count += 1
            active_now = self._active_tts_count

        start_time = time.time()
        success = False
        error: Optional[Exception] = None
        try:
            success = self._process_speak_request(job.text, job.interruptible, job.notify)
        except Exception as exc:
            error = exc
            logger.error(f"Error running TTS job: {exc}")
        finally:
            duration = time.time() - start_time
            with self._tts_count_lock:
                self._active_tts_count -= 1
                active_after = self._active_tts_count

        job.set_result(success, error)

        self._log_performance(duration, success, active_after)

        try:
            queue_size = self._tts_queue.qsize()
        except NotImplementedError:
            queue_size = 0

        logger.debug(
            "TTS job finished",
            extra={
                'tts_active_before': active_now,
                'tts_active_after': active_after,
                'tts_queue_size': queue_size,
            },
        )

    def init_engine(self):
        if self._initialized:
            logger.info(f"🎤 TTS ENGINE ALREADY INITIALIZED: type={self.engine_type}")
            return
        self._initialized = True
        # Piper-only engine
        try:
            logger.info(f"🎤 INITIALIZING TTS ENGINE...")
            from . import config as _C
            import piper  # noqa: F401
            from piper import PiperVoice
            
            # Use quantized model as primary, fallback to original
            voice_path = _C.get_piper_voice_path()
            fallback_path = _C.get_piper_fallback_path()
            
            logger.info(f"🎤 VOICE PATHS: primary={voice_path}, fallback={fallback_path}")
            logger.info(f"🎤 PATH EXISTS: primary={os.path.exists(voice_path)}, fallback={os.path.exists(fallback_path) if fallback_path else False}")
            
            if os.path.exists(voice_path):
                # Use quantized model (now primary)
                logger.info(f"🎤 LOADING QUANTIZED MODEL: {voice_path}")
                self.engine = PiperVoice.load(voice_path)
                self.engine_type = "piper_quantized"
                self.piper_available = True
                logger.info(f"✅ Piper quantized ready: {voice_path} (70% smaller, 2-3x faster)")
                print(f"✅ Piper quantized ready: {voice_path} (70% smaller, 2-3x faster)")
            elif fallback_path and os.path.exists(fallback_path):
                # Fallback to original model
                logger.info(f"🎤 LOADING FALLBACK MODEL: {fallback_path}")
                self.engine = PiperVoice.load(fallback_path)
                self.engine_type = "piper"
                self.piper_available = True
                logger.info(f"✅ Piper fallback ready: {fallback_path} (original model)")
                print(f"✅ Piper fallback ready: {fallback_path} (original model)")
            else:
                logger.error(f"🎤 NO VOICE MODEL FOUND: primary={voice_path}, fallback={fallback_path}")
                raise ImportError(f"Piper voice model not found at {voice_path} or {fallback_path}")
        except Exception as e:
            logger.error(f"🎤 PIPER INIT FAILED: {e}")
            print(f"❌ Piper init failed: {e}")
            self.engine = None
            self.engine_type = None

        # Setup interrupt audio handler if possible
        if INTERRUPTION_ENABLED and self.engine is not None and self.audio_handler is None:
            try:
                from .audio_interrupt import get_audio_handler
                self.audio_handler = get_audio_handler()
            except Exception:
                # honor configured output device if present
                out_dev = CFG.get_audio_output_device()
                self.audio_handler = AudioInterruptHandler(sample_rate=TTS_SAMPLE_RATE, output_device=out_dev)
            if hasattr(self.audio_handler, 'vad_threshold'):
                self.audio_handler.vad_threshold = INTERRUPT_THRESHOLD

        # Start a lightweight heartbeat to reinit Piper if not loaded
        def _hb():
            while True:
                try:
                    if self.engine is None:
                        self._initialized = False
                        self.init_engine()
                except Exception:
                    pass
                time.sleep(max(5, self._reload_sec))

        try:
            threading.Thread(target=_hb, daemon=True).start()
        except Exception:
            pass

    def _ensure_rate(self, audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        try:
            if src_sr == dst_sr:
                return audio.astype(np.float32)
            import numpy as _np
            x = _np.arange(len(audio), dtype=_np.float32)
            new_len = max(1, int(len(audio) * (dst_sr / float(src_sr))))
            new_x = _np.linspace(0, max(1, len(audio) - 1), new_len)
            res = _np.interp(new_x, x, audio.astype(_np.float32)).astype(_np.float32)
            return res
        except Exception:
            return audio.astype(np.float32)

    def _process_speak_request(self, text: str, interruptible: bool, notify: bool) -> bool:
        """Internal implementation that performs the actual TTS work."""
        if not text.strip():
            return True

        logger.info(
            f"🎤 TTS SPEAK START: '{text[:50]}{'...' if len(text) > 50 else ''}' "
            f"(length: {len(text)} chars)"
        )
        logger.info(
            f"🎤 TTS ENGINE STATUS: type={self.engine_type}, loaded={self.engine is not None}, "
            f"initialized={self._initialized}"
        )
        logger.info(f"🎤 TTS PARAMS: interruptible={interruptible}, notify={notify}")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"🎤 TTS ATTEMPT {attempt + 1}/{max_retries}")
                result = self._speak_attempt(text, interruptible, notify)
                logger.info(f"🎤 TTS ATTEMPT {attempt + 1} RESULT: {result}")
                return result
            except Exception as e:
                logger.warning(f"🎤 TTS ATTEMPT {attempt + 1} FAILED: {e}")
                if attempt < max_retries - 1:
                    try:
                        logger.info(f"🎤 TTS REINITIALIZING ENGINE (attempt {attempt + 1})")
                        self._initialized = False
                        self.init_engine()
                        time.sleep(0.5)
                        logger.info(
                            f"🎤 TTS REINITIALIZATION COMPLETE: type={self.engine_type}, "
                            f"loaded={self.engine is not None}"
                        )
                    except Exception as reinit_error:
                        logger.error(f"Failed to reinitialize TTS engine: {reinit_error}")
                else:
                    logger.error("All TTS attempts failed, falling back to system TTS")
                    try:
                        return self._fallback_speak(text, notify)
                    except Exception as fallback_error:
                        logger.error(f"Fallback TTS also failed: {fallback_error}")
                        return False
        return False

    def enqueue_speak(self, text: str, interruptible: bool = False, notify: bool = True) -> TTSJob:
        """Enqueue a TTS request for asynchronous processing."""
        job = TTSJob(text, interruptible, notify)

        if not text.strip():
            job.set_result(True)
            return job

        if self._tts_shutdown.is_set() or not self._tts_workers:
            job.set_result(False, RuntimeError("TTS manager is not available"))
            return job

        try:
            self._tts_queue.put(job, timeout=TTS_QUEUE_TIMEOUT)
            with self.performance_lock:
                self.performance_stats['queued_jobs'] = self._tts_queue.qsize()
        except queue.Full as exc:
            logger.error(f"TTS queue is full: {exc}")
            job.set_result(False, exc)

        return job

    @track_resource("tts", "speak_operation")
    def speak(self, text: str, interruptible: bool = False, notify: bool = True) -> bool:
        """Speak text using the configured TTS engine."""
        job = self.enqueue_speak(text, interruptible=interruptible, notify=notify)
        if job.done():
            return job.success

        completed = job.wait()
        if not completed and not job.done():
            logger.warning("TTS job wait timed out")
            return False

        if job.error:
            logger.error(f"TTS job failed: {job.error}")

        return job.success

    def _speak_attempt(self, text: str, interruptible: bool, notify: bool) -> bool:
        """Single TTS attempt with proper error handling"""
        logger.info(f"🎤 _speak_attempt START: text='{text[:30]}...', interruptible={interruptible}, notify={notify}")
        
        if not self._initialized:
            logger.info(f"🎤 TTS NOT INITIALIZED, initializing...")
            self.init_engine()
        
        if not self.engine:
            logger.error(f"🎤 TTS ENGINE NOT LOADED after init attempt")
            raise RuntimeError("TTS engine not loaded")
        
        logger.info(f"🎤 TTS ENGINE READY: type={self.engine_type}, engine={type(self.engine)}")
        
        if notify:
            logger.info(f"🎤 NOTIFYING DASHBOARD: speaking_started")
            _notify_dashboard_state('speaking_started')

        if self.engine_type in ["piper", "piper_quantized"]:
            logger.info(f"🎤 USING PIPER TTS: type={self.engine_type}")
            result = self._speak_with_piper(text, interruptible, notify)
            logger.info(f"🎤 PIPER TTS RESULT: {result}")
            return result
        else:
            logger.error(f"🎤 UNSUPPORTED TTS ENGINE TYPE: {self.engine_type}")
            raise RuntimeError("No TTS engine available")

    def _speak_with_piper(self, text: str, interruptible: bool, notify: bool) -> bool:
        """Speak using Piper TTS with error recovery and caching"""
        try:
            logger.info(f"🎤 PIPER TTS START: text='{text[:50]}...', interruptible={interruptible}, notify={notify}")
            
            # Check cache first
            cached_audio = self._get_cached_audio(text)
            if cached_audio is not None:
                logger.info(f"🎯 TTS Cache HIT for: '{text[:30]}...'")
                self._log_cache_stats(True)
                return self._play_cached_audio(cached_audio, interruptible, notify)
            
            logger.info(f"🔄 TTS Cache MISS for: '{text[:30]}...'")
            self._log_cache_stats(False)
            
            from piper import SynthesisConfig
            sr = CFG.get_piper_sample_rate()
            config = SynthesisConfig()
            
            # Optimize for speed over quality
            config.length_scale = 1.0 / SPEED if SPEED > 0 else 1.0
            config.noise_scale = 0.5  # Reduced for faster synthesis
            try:
                config.noise_w = 0.6      # type: ignore  # Reduced for faster synthesis
                config.phoneme_silence_sec = 0.05  # type: ignore  # Reduced silence for faster output
            except AttributeError:
                # Some Piper versions don't have these attributes
                pass
            
            logger.info(f"🎤 PIPER CONFIG: length_scale={config.length_scale}, noise_scale={config.noise_scale}, noise_w={getattr(config, 'noise_w', 'N/A')}")
            
            # Synthesize audio (returns generator)
            logger.info(f"🎤 CALLING PIPER SYNTHESIZE...")
            audio_chunks = self.engine.synthesize(text, config)  # type: ignore
            logger.info(f"🎤 PIPER SYNTHESIZE RETURNED: {type(audio_chunks)}")
            
            # Process audio chunks from generator
            audio_arrays = []
            chunk_count = 0
            for ch in audio_chunks:
                chunk_count += 1
                logger.info(f"🎤 PROCESSING CHUNK {chunk_count}: {type(ch)}")
                try:
                    # Convert chunk to numpy array
                    if hasattr(ch, 'audio_float_array'):
                        audio_arrays.append(ch.audio_float_array)
                        logger.info(f"🎤 CHUNK {chunk_count}: audio_float_array, shape={ch.audio_float_array.shape}")
                    elif hasattr(ch, 'audio'):
                        audio_data = np.array(ch.audio, dtype=np.float32)  # type: ignore
                        audio_arrays.append(audio_data)
                        logger.info(f"🎤 CHUNK {chunk_count}: audio, shape={audio_data.shape}")
                    else:
                        # Try to convert directly
                        audio_arrays.append(np.array(ch, dtype=np.float32))
                        logger.info(f"🎤 CHUNK {chunk_count}: direct conversion, shape={np.array(ch).shape}")
                except Exception as e:
                    logger.warning(f"🎤 CHUNK {chunk_count} PROCESSING FAILED: {e}")
                    continue
            
            logger.info(f"🎤 PROCESSED {chunk_count} CHUNKS, {len(audio_arrays)} SUCCESSFUL")
            
            if not audio_arrays:
                logger.warning("🎤 NO AUDIO GENERATED FROM PIPER")
                if notify:
                    _notify_dashboard_state('speaking_ended')
                return False
            
            # Concatenate all audio arrays
            logger.info(f"🎤 CONCATENATING {len(audio_arrays)} AUDIO ARRAYS...")
            audio_arr = np.concatenate(audio_arrays).astype(np.float32)
            logger.info(f"🎤 CONCATENATED AUDIO SHAPE: {audio_arr.shape}, DURATION: {len(audio_arr) / sr:.2f}s")
            
            # Cache the audio for future use
            self._cache_audio(text, audio_arr)
            
            # Play audio
            logger.info(f"🎤 PLAYING AUDIO: interruptible={interruptible}, audio_handler={self.audio_handler is not None}")
            if self.audio_handler and interruptible:
                logger.info(f"🎤 USING INTERRUPTIBLE AUDIO HANDLER")
                audio_arr = self._ensure_rate(audio_arr, sr, TTS_SAMPLE_RATE)
                logger.info(f"🎤 AUDIO RATE CONVERTED: {len(audio_arr)} samples at {TTS_SAMPLE_RATE}Hz")
                ok = self.audio_handler.play_audio(audio_arr)
                logger.info(f"🎤 INTERRUPTIBLE PLAYBACK RESULT: {ok}")
                if notify:
                    _notify_dashboard_state('speaking_ended' if ok else 'speaking_interrupted')
                return ok
            else:
                logger.info(f"🎤 USING SOUNDDEVICE PLAYBACK")
                result = self._play_audio_sounddevice(audio_arr, sr, notify)
                logger.info(f"🎤 SOUNDDEVICE PLAYBACK RESULT: {result}")
                return result
                
        except ImportError as e:
            logger.error(f"Piper import failed: {e}")
            raise RuntimeError("Piper TTS not available")
        except Exception as e:
            logger.error(f"Piper synthesis failed: {e}")
            raise

    def _play_audio_sounddevice(self, audio_arr: np.ndarray, sample_rate: int, notify: bool) -> bool:
        """Play audio using sounddevice with error recovery"""
        try:
            logger.info(f"🎤 SOUNDDEVICE PLAY START: shape={audio_arr.shape}, sample_rate={sample_rate}, notify={notify}")
            import sounddevice as sd
            logger.info(f"🎤 CALLING sd.play()...")
            sd.play(audio_arr, samplerate=sample_rate)
            logger.info(f"🎤 CALLING sd.wait()...")
            sd.wait()
            logger.info(f"🎤 SOUNDDEVICE PLAY COMPLETE")
            if notify:
                _notify_dashboard_state('speaking_ended')
            return True
        except Exception as e:
            logger.error(f"Sounddevice playback failed: {e}")
            raise

    def _fallback_speak(self, text: str, notify: bool) -> bool:
        """Fallback to system TTS when primary TTS fails"""
        try:
            if notify:
                _notify_dashboard_state('speaking_started')
            
            # Try system 'say' command as fallback
            import subprocess
            result = subprocess.run(['say', text], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                if notify:
                    _notify_dashboard_state('speaking_ended')
                return True
            else:
                logger.error(f"System TTS failed: {result.stderr}")
                if notify:
                    _notify_dashboard_state('speaking_ended')
                return False
        except Exception as e:
            logger.error(f"Fallback TTS failed: {e}")
            if notify:
                _notify_dashboard_state('speaking_ended')
            return False

    def interrupt(self):
        """Interrupt current speech"""
        if self.audio_handler:
            self.audio_handler.interrupt_playback()
            logger.info("TTS playback interrupted")
    
    def _log_performance(self, duration: float, success: bool = True, active_jobs: Optional[int] = None):
        """Log TTS performance metrics"""
        with self.performance_lock:
            self.performance_stats['total_requests'] += 1
            self.performance_stats['last_request_time'] = time.time()

            if success:
                self.performance_stats['total_duration'] += duration
                self.performance_stats['max_duration'] = max(self.performance_stats['max_duration'], duration)
                self.performance_stats['min_duration'] = min(self.performance_stats['min_duration'], duration)
                self.performance_stats['avg_duration'] = (
                    self.performance_stats['total_duration'] /
                    max(1, self.performance_stats['total_requests'])
                )
            else:
                self.performance_stats['errors'] += 1

            if active_jobs is None:
                active_jobs = self._active_tts_count
            self.performance_stats['active_jobs'] = max(0, active_jobs)
            try:
                queue_size = self._tts_queue.qsize()
            except NotImplementedError:
                queue_size = 0
            self.performance_stats['queued_jobs'] = max(0, queue_size)
    
    def get_performance_stats(self) -> dict:
        """Get current performance statistics"""
        with self.performance_lock:
            stats = self.performance_stats.copy()
            # Add system resource usage
            try:
                process = psutil.Process()
                stats['cpu_percent'] = process.cpu_percent()
                stats['memory_mb'] = process.memory_info().rss / 1024 / 1024
                stats['memory_percent'] = process.memory_percent()
            except Exception:
                stats['cpu_percent'] = 0
                stats['memory_mb'] = 0
                stats['memory_percent'] = 0
            
            # Add cache statistics
            with self._cache_lock:
                stats['cache_size'] = len(self._tts_cache)
                stats['cache_hits'] = self.performance_stats.get('cache_hits', 0)
                stats['cache_misses'] = self.performance_stats.get('cache_misses', 0)
            
            return stats
    
    def _get_cached_audio(self, text: str) -> Optional[np.ndarray]:
        """Get cached audio for text if available"""
        if not self._cache_enabled:
            return None
        with self._cache_lock:
            return self._tts_cache.get(text)
    
    def _cache_audio(self, text: str, audio: np.ndarray) -> None:
        """Cache audio for text with memory monitoring"""
        if not self._cache_enabled:
            return

        try:
            with self._cache_lock:
                # Check memory usage before adding
                current_size = len(self._tts_cache)
                if current_size >= self._cache_max_size:
                    # Remove oldest entries to make room
                    remove_count = max(1, current_size // 4)  # Remove 25% of entries
                    keys_to_remove = list(self._tts_cache.keys())[:remove_count]
                    for key in keys_to_remove:
                        del self._tts_cache[key]
                    logger.debug(f"🧹 Removed {remove_count} old cache entries")

                self._tts_cache[text] = audio.copy()

                # Log memory usage periodically
                if current_size % 10 == 0:  # Every 10 cache operations
                    memory_usage = self.get_memory_usage()
                    logger.debug(f"📊 TTS cache memory usage: {memory_usage}")

        except Exception as e:
            logger.warning(f"Failed to cache audio: {e}")
            # If caching fails, try to free some memory
            try:
                with self._cache_lock:
                    # Clear half the cache as emergency cleanup
                    cache_keys = list(self._tts_cache.keys())
                    remove_count = len(cache_keys) // 2
                    for key in cache_keys[:remove_count]:
                        del self._tts_cache[key]
                    logger.info(f"🧹 Emergency cache cleanup: removed {remove_count} entries")
            except Exception as cleanup_e:
                logger.error(f"Emergency cache cleanup failed: {cleanup_e}")
    
    def _log_cache_stats(self, hit: bool) -> None:
        """Log cache hit/miss statistics"""
        with self.performance_lock:
            if hit:
                self.performance_stats['cache_hits'] = self.performance_stats.get('cache_hits', 0) + 1
            else:
                self.performance_stats['cache_misses'] = self.performance_stats.get('cache_misses', 0) + 1
    
    def _play_cached_audio(self, audio: np.ndarray, interruptible: bool, notify: bool) -> bool:
        """Play cached audio with optimized performance"""
        try:
            sr = CFG.get_piper_sample_rate()
            
            # Play audio
            if self.audio_handler and interruptible:
                audio = self._ensure_rate(audio, sr, TTS_SAMPLE_RATE)
                ok = self.audio_handler.play_audio(audio)
                if notify:
                    _notify_dashboard_state('speaking_ended' if ok else 'speaking_interrupted')
                return ok
            else:
                return self._play_audio_sounddevice(audio, sr, notify)
        except Exception as e:
            logger.error(f"Error playing cached audio: {e}")
            return False
    
    def _detect_mps_support(self) -> bool:
        """Detect if Metal Performance Shaders (MPS) is available"""
        try:
            if platform.system() != "Darwin":
                return False
            
            # Check for Apple Silicon
            import subprocess
            result = subprocess.run(['uname', '-m'], capture_output=True, text=True)
            if result.returncode == 0 and 'arm' in result.stdout.lower():
                # Check if MPS is available in PyTorch (if installed)
                try:
                    import torch
                    return torch.backends.mps.is_available()
                except ImportError:
                    return False
            return False
        except Exception:
            return False
    
    def _detect_coreml_support(self) -> bool:
        """Detect if CoreML is available for optimization"""
        try:
            if platform.system() != "Darwin":
                return False
            
            # Check for CoreML framework
            import subprocess
            result = subprocess.run(['python', '-c', 'import coremltools'], 
                                  capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False

    def cleanup(self) -> None:
        """Clean up resources to prevent memory leaks"""
        try:
            logger.info("🧹 Cleaning up TTS manager resources...")

            self._tts_shutdown.set()

            # Drain pending queue items and mark them cancelled
            cancelled_jobs = 0
            while True:
                try:
                    job = self._tts_queue.get_nowait()
                except queue.Empty:
                    break

                if isinstance(job, TTSJob):
                    if not job.done():
                        job.set_result(False, RuntimeError("TTS manager shutting down"))
                    cancelled_jobs += 1
                self._tts_queue.task_done()

            if cancelled_jobs:
                logger.info(f"🧹 Cancelled {cancelled_jobs} queued TTS jobs")

            # Signal workers to exit after finishing current task
            for _ in list(self._tts_workers):
                self._tts_queue.put(None)

            for worker in list(self._tts_workers):
                worker.join(timeout=5.0)
                if worker.is_alive():
                    logger.warning(f"⚠️ TTS worker {worker.name} did not stop cleanly")

            self._tts_workers.clear()

            # Clear TTS cache
            with self._cache_lock:
                cache_size = len(self._tts_cache)
                self._tts_cache.clear()
                logger.info(f"🧹 Cleared TTS cache ({cache_size} entries)")

            # Clear performance stats
            with self.performance_lock:
                self.performance_stats = self._default_performance_stats()

            with self._tts_count_lock:
                if self._active_tts_count:
                    logger.debug(
                        "Waiting for active TTS jobs to settle",
                        extra={'active_jobs': self._active_tts_count},
                    )
                self._active_tts_count = 0

            logger.info("✅ TTS manager cleanup completed")

        except Exception as e:
            logger.error(f"Error during TTS manager cleanup: {e}")

    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage statistics"""
        try:
            cache_size = 0
            with self._cache_lock:
                cache_size = len(self._tts_cache)

            # Estimate memory usage (approximate)
            estimated_cache_memory = cache_size * 1024 * 1024  # Rough estimate: 1MB per cache entry

            return {
                'tts_cache_entries': cache_size,
                'estimated_cache_memory_mb': estimated_cache_memory // (1024 * 1024),
                'active_tts_threads': self._active_tts_count
            }
        except Exception as e:
            logger.error(f"Error getting memory usage: {e}")
            return {'error': str(e)}

# Initialize TTS manager
tts_manager = TTSManager()

# ---- Interruptible Conversation System ----
if INTERRUPTION_ENABLED:
    conversation_manager = ConversationManager(
        max_history=CONTEXT_BUFFER_SIZE,
        context_timeout=CONVERSATION_TIMEOUT
    )

    # Register conversation state callback for audio interruption
    def on_conversation_state_change(context: ConversationContext):
        """Handle conversation state changes"""
        if context.current_state == ConversationState.INTERRUPTED:
            tts_manager.interrupt()
            print("🎤 Conversation interrupted by user")
            _notify_dashboard_state('speaking_interrupted')

    conversation_manager.register_state_callback(on_conversation_state_change)

    # Message bus client - initialized later when voice assistant starts
    bus_client = None

else:
    # Fallback for when interruption is disabled
    conversation_manager = None
    bus_client = None

def speak(text: str):
    """Speak text using the unified TTS system with enhanced error handling and performance monitoring"""
    if not text.strip():
        logger.warning("Empty text provided to speak function")
        return
    
    start_time = time.time()
    success = False
    
    try:
        logger.info(f"🎤 SPEAK FUNCTION START: '{text[:50]}{'...' if len(text) > 50 else ''}' (length: {len(text)} chars)")
        logger.info(f"🎤 INTERRUPTION_ENABLED: {INTERRUPTION_ENABLED}, conversation_manager: {conversation_manager is not None}")
        
        if INTERRUPTION_ENABLED and conversation_manager:
            logger.info(f"🎤 USING INTERRUPTIBLE TTS PATH")
            # Start conversation response tracking
            conversation_manager.start_response(text)

            # Use interruptible TTS
            _notify_dashboard_state('speaking_started')
            logger.info(f"🎤 CALLING tts_manager.speak() with interruptible=True")
            completed = tts_manager.speak(text, interruptible=True, notify=False)
            logger.info(f"🎤 tts_manager.speak() RESULT: {completed}")

            if completed:
                logger.info(f"🎤 TTS COMPLETED SUCCESSFULLY")
                conversation_manager.update_response(text, is_complete=True)
                _notify_dashboard_state('speaking_ended')
                success = True
            else:
                logger.info(f"🎤 TTS WAS INTERRUPTED")
                # TTS was interrupted - only interrupt if not already interrupted
                with conversation_manager.lock:
                    if (conversation_manager.current_context and
                        conversation_manager.current_context.current_state != ConversationState.INTERRUPTED):
                        conversation_manager.interrupt_response()
                _notify_dashboard_state('speaking_interrupted')

        else:
            logger.info(f"🎤 USING NON-INTERRUPTIBLE TTS PATH")
            # Use non-interruptible TTS
            _notify_dashboard_state('speaking_started')
            logger.info(f"🎤 CALLING tts_manager.speak() with interruptible=False")
            completed = tts_manager.speak(text, interruptible=False, notify=False)
            logger.info(f"🎤 tts_manager.speak() RESULT: {completed}")
            _notify_dashboard_state('speaking_ended')
            success = completed
            
    except Exception as e:
        logger.error(f"Error in speak function: {e}")
        _notify_dashboard_state('speaking_ended')
        # Try to recover by reinitializing TTS if needed
        try:
            if tts_manager and tts_manager.engine is None:
                logger.info("Attempting to reinitialize TTS engine after error")
                tts_manager.init_engine()
        except Exception as recovery_error:
            logger.error(f"Failed to recover TTS engine: {recovery_error}")
    finally:
        # Log performance metrics
        duration = time.time() - start_time
        stats_snapshot = tts_manager.get_performance_stats()

        total_requests = stats_snapshot.get('total_requests', 0)
        if total_requests and total_requests % 10 == 0:
            logger.info(
                f"📊 TTS Performance: Avg={stats_snapshot.get('avg_duration', 0.0):.2f}s, "
                f"Max={stats_snapshot.get('max_duration', 0.0):.2f}s, "
                f"CPU={stats_snapshot.get('cpu_percent', 0.0):.1f}%, "
                f"RAM={stats_snapshot.get('memory_mb', 0.0):.1f}MB, "
                f"Errors={stats_snapshot.get('errors', 0)}"
            )

        logger.info(
            f"⏱️ TTS Duration: {duration:.2f}s ({'✅' if success else '❌'}) "
            f"[active={stats_snapshot.get('active_jobs', 0)}, queued={stats_snapshot.get('queued_jobs', 0)}]"
        )

"""Web GUI removed from voice_assistant; use web_dashboard service instead."""

# ---- Main loop ----
def main():
    global TTS_STREAMED
    # Ensure ResponseState is available
    from .conversation_manager import ResponseState
    print("🚀 Starting MacBot Voice Assistant...")
    print("Local Voice AI ready. Speak after the beep. (Ctrl+C to quit)")
    print("💡 Try saying:")
    print("   • 'search for weather' - Web search")
    print("   • 'browse example.com' - Open website")
    print("   • 'open app safari' - Launch applications")
    print("   • 'take screenshot' - Capture screen")
    print("   • 'system info' - System status")
    print("🌐 Tip: Start the web dashboard via 'macbot-dashboard' for UI.")

    # Initialize TTS engine in main process
    try:
        tts_manager.init_engine()
        print(f"✅ TTS engine ready: {tts_manager.engine_type}")
    except Exception as e:
        print(f"❌ TTS engine init failed: {e}")

    # Initialize message bus client for interruption signals (if enabled)
    global bus_client
    if INTERRUPTION_ENABLED and bus_client is None:
        try:
            bus_client = MessageBusClient(service_type="voice_assistant")
            bus_client.start()

            # Register handler for interruption messages from web dashboard
            def handle_interruption_message(message: Dict):
                """Handle interruption messages from other services"""
                source = message.get('source', 'unknown')
                logger.info(f"Received interruption signal from {source}")

                # Interrupt current conversation if active
                if conversation_manager and conversation_manager.current_context:
                    conversation_manager.interrupt_response()
                    print(f"🎤 Conversation interrupted by {source}")

            bus_client.register_handler('interruption', handle_interruption_message)
            print("✅ Message bus client connected")

        except Exception as e:
            logger.warning(f"Failed to connect to message bus: {e}")
            print("⚠️ Message bus connection failed - running without external interruption support")

    # Start lightweight HTTP control server for health/interrupt
    try:
        from flask import Flask, jsonify, request
        try:
            from ..auth import require_auth, optional_auth, get_auth_manager  # type: ignore
        except ImportError:
            # Fallback for when auth module is not available
            require_auth = lambda f: f  # type: ignore
            optional_auth = lambda f: f  # type: ignore
            get_auth_manager = lambda: None  # type: ignore
        control_app = Flask("macbot_voice_control")
        try:
            from flask_cors import CORS
            CORS(control_app, origins=["http://127.0.0.1:3000", "http://localhost:3000"])  # UI access
        except Exception:
            pass

        @control_app.route('/health')
        def _control_health():
            return jsonify({
                'status': 'ok',
                'interruption_enabled': INTERRUPTION_ENABLED,
                'timestamp': time.time()
            })

        @control_app.route('/interrupt', methods=['POST'])
        @require_auth
        def _control_interrupt():
            try:
                if INTERRUPTION_ENABLED and conversation_manager and conversation_manager.current_context:
                    conversation_manager.interrupt_response()
                else:
                    # Even if not in speaking state, trigger TTS interrupt if active
                    tts_manager.interrupt()
                return jsonify({'status': 'ok'}), 200
            except Exception as e:
                logger.error(f"Control interrupt error: {e}")
                return jsonify({'status': 'error', 'error': str(e)}), 500

        @control_app.route('/speak', methods=['POST'])
        @require_auth
        def _control_speak():
            try:
                data = request.get_json() or {}
                text = str(data.get('text', '')).strip()
                logger.info(f"TTS request received: '{text[:50]}{'...' if len(text) > 50 else ''}'")
                if not text:
                    return jsonify({'ok': False, 'error': 'text required'}), 400
                # ensure TTS is ready
                if tts_manager.engine is None or tts_manager.engine_type not in ['piper', 'piper_quantized']:
                    logger.info("TTS engine not ready, initializing...")
                    try:
                        tts_manager.init_engine()
                    except Exception as e:
                        logger.error(f"TTS engine init failed: {e}")
                        pass
                if tts_manager.engine is None:
                    logger.error("TTS engine still not loaded after init attempt")
                    return jsonify({'ok': False, 'error': 'TTS engine not loaded'}), 503
                # speak asynchronously so HTTP doesn't block on long TTS
                logger.info("Starting TTS playback...")
                threading.Thread(target=speak, args=(text,), daemon=True).start()
                return jsonify({'ok': True})
            except Exception as e:
                logger.error(f"Control speak error: {e}")
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/tts-performance')
        def _control_tts_performance():
            """Get TTS performance statistics"""
            try:
                stats = tts_manager.get_performance_stats()
                return jsonify({
                    'ok': True,
                    'performance': stats,
                    'engine_type': tts_manager.engine_type,
                    'engine_loaded': tts_manager.engine is not None
                })
            except Exception as e:
                logger.error(f"TTS performance error: {e}")
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/mic-check', methods=['POST'])
        def _control_mic_check():
            """Attempt to open a short-lived input stream to trigger OS mic permission.
            Returns JSON with success/error for guidance."""
            try:
                if os.environ.get('MACBOT_NO_AUDIO') == '1':
                    return jsonify({'ok': False, 'error': 'audio disabled via env'}), 400
                if sd is None:
                    return jsonify({'ok': False, 'error': 'sounddevice not available'}), 500
                # Try to open and immediately close a short input stream
                with sd.InputStream(channels=1, samplerate=SAMPLE_RATE, dtype='float32'):
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                logger.warning(f"Mic check failed: {e}")
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/devices')
        def _control_devices():
            try:
                if sd is None:
                    return jsonify({'ok': False, 'error': 'sounddevice not available'}), 500
                devices = sd.query_devices()
                default = sd.default.device
                return jsonify({'ok': True, 'devices': devices, 'default': default})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/set-output', methods=['POST'])
        def _control_set_output():
            try:
                if sd is None:
                    return jsonify({'ok': False, 'error': 'sounddevice not available'}), 500
                data = request.get_json() or {}
                dev = data.get('device')
                cur_in, cur_out = sd.default.device if sd.default.device else (None, None)
                sd.default.device = (cur_in, dev)
                # Update audio handler if exists
                if tts_manager and tts_manager.audio_handler:
                    tts_manager.audio_handler.output_device = dev
                # Persist to config file
                try:
                    import yaml
                    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'config.yaml'))
                    data = {}
                    if os.path.exists(cfg_path):
                        with open(cfg_path) as f:
                            data = yaml.safe_load(f) or {}
                    data.setdefault('voice_assistant', {}).setdefault('audio', {})['output_device'] = dev
                    with open(cfg_path, 'w') as f:
                        yaml.safe_dump(data, f)
                except Exception as e:
                    logger.warning(f"Failed to persist output device: {e}")
                return jsonify({'ok': True, 'default': list(sd.default.device) if sd.default.device else None})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/voices')
        def _control_voices():
            """List available Piper voices by scanning piper_voices/*/model.onnx"""
            try:
                base = os.path.abspath(os.path.join(os.getcwd(), 'piper_voices'))
                voices = []
                if os.path.isdir(base):
                    for root, dirs, files in os.walk(base):
                        if 'model.onnx' in files:
                            vp = os.path.join(root, 'model.onnx')
                            name = os.path.basename(root)
                            voices.append({'name': name, 'path': vp})
                cur = CFG.get_piper_voice_path()
                return jsonify({'ok': True, 'voices': voices, 'current': cur})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/set-voice', methods=['POST'])
        def _control_set_voice():
            try:
                data = request.get_json() or {}
                vp = str(data.get('voice_path') or '').strip()
                if not vp or not os.path.exists(vp):
                    return jsonify({'ok': False, 'error': 'voice_path invalid'}), 400
                # persist
                try:
                    import yaml
                    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'config.yaml'))
                    y = {}
                    if os.path.exists(cfg_path):
                        with open(cfg_path) as f:
                            y = yaml.safe_load(f) or {}
                    y.setdefault('models', {}).setdefault('tts', {}).setdefault('piper', {})['voice_path'] = vp
                    with open(cfg_path, 'w') as f:
                        yaml.safe_dump(y, f)
                except Exception as e:
                    logger.warning(f"Failed to persist voice_path: {e}")
                # force reload
                try:
                    tts_manager.engine = None
                    tts_manager.engine_type = None
                    tts_manager._initialized = False
                    tts_manager.init_engine()
                except Exception:
                    pass
                return jsonify({'ok': True, 'voice_path': vp})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/set-llm-max-tokens', methods=['POST'])
        def _control_set_llm_max_tokens():
            try:
                data = request.get_json() or {}
                mt = int(data.get('max_tokens', 0))
                if mt <= 0 or mt > 8192:
                    return jsonify({'ok': False, 'error': 'max_tokens must be in (0, 8192]'}), 400
                # persist
                try:
                    import yaml
                    cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'config.yaml'))
                    y = {}
                    if os.path.exists(cfg_path):
                        with open(cfg_path) as f:
                            y = yaml.safe_load(f) or {}
                    y.setdefault('models', {}).setdefault('llm', {})['max_tokens'] = mt
                    with open(cfg_path, 'w') as f:
                        yaml.safe_dump(y, f)
                    try:
                        CFG.reload_config()
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Failed to persist max_tokens: {e}")
                # set runtime
                try:
                    global LLAMA_MAXTOK
                    LLAMA_MAXTOK = mt
                except Exception:
                    pass
                return jsonify({'ok': True, 'max_tokens': mt})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/preview-voice', methods=['POST'])
        def _control_preview_voice():
            try:
                data = request.get_json() or {}
                text = str(data.get('text') or 'Hey there, how can I help?')
                tts_manager.enqueue_speak(text, interruptible=False, notify=False)
                return jsonify({'ok': True})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 500

        @control_app.route('/info')
        def _control_info():
            try:
                convo = None
                try:
                    if INTERRUPTION_ENABLED and conversation_manager:
                        convo = conversation_manager.get_conversation_summary()
                except Exception:
                    convo = None

                # Piper-only reporting
                planned_engine = 'piper' if os.path.exists(CFG.get_piper_voice_path()) else None
                engine_loaded = (tts_manager.engine_type in ['piper', 'piper_quantized'] and tts_manager.engine is not None)

                return jsonify({
                    'stt': {
                        'impl': _WHISPER_IMPL,
                        'model': WHISPER_MODEL,
                        'language': WHISPER_LANG
                    },
                    'tts': {
                        'engine': planned_engine,
                        'voice': VOICE,
                        'speed': SPEED,
                        'voices': getattr(tts_manager, 'voices', []),
                        'engine_loaded': engine_loaded,
                        'voice_path': CFG.get_piper_voice_path()
                    },
                    'interruption': {
                        'enabled': INTERRUPTION_ENABLED,
                        'threshold': INTERRUPT_THRESHOLD,
                        'cooldown': INTERRUPT_COOLDOWN,
                        'conversation_timeout': CONVERSATION_TIMEOUT,
                        'context_buffer_size': CONTEXT_BUFFER_SIZE
                    },
                    'audio': {
                        'sample_rate': SAMPLE_RATE,
                        'block_sec': BLOCK_DUR,
                        'vad_threshold': VAD_THRESH,
                        'devices_default': (list(sd.default.device) if sd and sd.default.device else None)
                    },
                    'conversation': convo
                })
            except Exception as e:
                logger.error(f"Control info error: {e}")
                return jsonify({'error': str(e)}), 500

        def _run_control():
            try:
                control_app.run(host=VA_HOST, port=VA_PORT, debug=False, use_reloader=False)
            except Exception as e:
                logger.warning(f"Voice assistant control server failed to start: {e}")

        threading.Thread(target=_run_control, daemon=True).start()
        logger.info(f"Voice assistant control server on http://{VA_HOST}:{VA_PORT}")
    except Exception as e:
        logger.warning(f"Voice assistant control server not started: {e}")

    # Proactively init Piper so /info and /speak reflect ready state
    try:
        tts_manager.init_engine()
        logger.info("TTS engine initialized successfully")
    except Exception as e:
        logger.warning(f"Piper init deferred: {e}")

    # Initialize audio input; run in text-only mode if unavailable
    stream = None
    try:
        if os.environ.get('MACBOT_NO_AUDIO') == '1':
            raise RuntimeError('audio disabled via MACBOT_NO_AUDIO')
        if sd is None:
            raise RuntimeError('sounddevice not available')
        try:
            # Test audio system with silent output (no sound)
            sd.play(np.zeros(1200), samplerate=TTS_SAMPLE_RATE, blocking=True)
            logger.debug("Audio system test completed (silent)")
        except Exception as e:
            logger.debug(f"Audio system test failed: {e}")
        stream = sd.InputStream(
            channels=1,
            samplerate=SAMPLE_RATE,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * BLOCK_DUR),
            callback=_callback,
        )
        stream.start()
        logger.info("Audio input stream started")
    except Exception as e:
        logger.warning(f"Audio initialization failed; running in text-only mode: {e}")

    voiced = False
    stream_tr = StreamingTranscriber(sample_rate=SAMPLE_RATE)
    last_voice = time.time()

    try:
        while True:
            if stream is None:
                time.sleep(1.0)
                continue
            block = audio_q.get()
            # Optimize: avoid reshape by working with the original shape
            # is_voiced can handle multi-dimensional arrays
            v = is_voiced(block)
            now = time.time()
            
            # Performance optimization: skip processing if we're in a speaking state and not interrupted
            if (INTERRUPTION_ENABLED and conversation_manager and 
                conversation_manager.current_context and
                conversation_manager.current_context.current_state == ConversationState.SPEAKING and
                conversation_manager.current_context.response_state != ResponseState.INTERRUPTED):
                continue

            if v:
                voiced = True
                last_voice = now
                # Only reshape when needed for the transcriber
                block_flat = block.reshape(-1)
                delta = stream_tr.add_chunk(block_flat)
                if delta:
                    print(delta, end="", flush=True)
            elif voiced:
                # candidate end-of-turn
                if HAS_TURN_DETECT:
                    # simple delay + confirm (for demo)
                    if now - last_voice > TURNING_DELAY:
                        transcript = stream_tr.flush()
                        voiced = False
                        if transcript:
                            print(f"\n[YOU] {transcript}")
                            msg_id = str(uuid.uuid4())
                            logger.info(f"va_chat_in id={msg_id} len={len(transcript)} preview={transcript[:80]!r}")
                            
                            # Validate input before processing
                            if not validate_input(transcript):
                                print("[BOT] Invalid input received\n")
                                continue
                                
                            if INTERRUPTION_ENABLED and conversation_manager:
                                conversation_manager.start_conversation()
                                conversation_manager.add_user_input(transcript)

                            # Check if LLM service is available, otherwise use degraded mode
                            service_available = check_llm_service_available()

                            if service_available:
                                reply = llama_chat(transcript)
                            else:
                                logger.warning("LLM service unavailable, using degraded mode")
                                reply = get_degraded_response(transcript)

                            print(f"[BOT] {reply}\n")
                            logger.info(f"va_chat_out reply_to={msg_id} len={len(reply)} preview={reply[:80]!r}")
                            # Avoid duplicate speech if streaming TTS already occurred
                            if not TTS_STREAMED:
                                speak(reply)
                else:
                    if now - last_voice > SILENCE_HANG:
                        transcript = stream_tr.flush()
                        voiced = False
                        if transcript:
                            print(f"\n[YOU] {transcript}")
                            msg_id = str(uuid.uuid4())
                            logger.info(f"va_chat_in id={msg_id} len={len(transcript)} preview={transcript[:80]!r}")
                            
                            # Validate input before processing
                            if not validate_input(transcript):
                                print("[BOT] Invalid input received\n")
                                continue
                                
                            if INTERRUPTION_ENABLED and conversation_manager:
                                conversation_manager.start_conversation()
                                conversation_manager.add_user_input(transcript)

                            # Check if LLM service is available, otherwise use degraded mode
                            service_available = check_llm_service_available()

                            if service_available:
                                reply = llama_chat(transcript)
                            else:
                                logger.warning("LLM service unavailable, using degraded mode")
                                reply = get_degraded_response(transcript)

                            print(f"[BOT] {reply}\n")
                            logger.info(f"va_chat_out reply_to={msg_id} len={len(reply)} preview={reply[:80]!r}")
                            if not TTS_STREAMED:
                                speak(reply)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        # Clean up message bus client
        if INTERRUPTION_ENABLED and bus_client:
            try:
                bus_client.stop()
                print("✅ Message bus client disconnected")
            except Exception as e:
                logger.warning(f"Error stopping message bus client: {e}")

        # Clean up TTS manager to prevent memory leaks
        try:
            tts_manager.cleanup()
            print("✅ TTS manager cleaned up")
        except Exception as e:
            logger.warning(f"Error cleaning up TTS manager: {e}")

if __name__ == "__main__":
    try:
        # Log any uncaught exceptions for diagnosis rather than hard-crashing
        def _hook(exctype, value, tb):
            try:
                import traceback
                logger.error("Uncaught exception in voice assistant:\n" + ''.join(traceback.format_exception(exctype, value, tb)))
            except Exception:
                pass
        try:
            sys.excepthook = _hook
        except Exception:
            pass
        main()
    except Exception as e:
        try:
            import traceback
            logger.error("Fatal error in voice assistant:\n" + ''.join(traceback.format_exc()))
        except Exception:
            pass
        raise

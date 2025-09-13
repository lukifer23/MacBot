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
import psutil
import logging
from .logging_utils import setup_logger
from typing import Dict, List, Optional
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

# Configure logging
logger = setup_logger("macbot.voice_assistant", "logs/voice_assistant.log")

# Dashboard notifications
_WD_HOST, _WD_PORT = CFG.get_web_dashboard_host_port()
VA_HOST, VA_PORT = CFG.get_voice_assistant_host_port()

def _notify_dashboard_state(event_type: str, message: str = "") -> None:
    """Best-effort POST to dashboard to inform of assistant state changes."""
    try:
        url = f"http://{_WD_HOST}:{_WD_PORT}/api/assistant-event"
        payload = {"type": event_type}
        if message:
            payload["message"] = message
        requests.post(url, json=payload, timeout=1.0)
    except Exception as e:
        logger.debug(f"Dashboard notify failed: {e}")

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
    def __init__(self):
        self.tools = {
            "web_search": tools.web_search,
            "browse_website": tools.browse_website,
            "get_system_info": tools.get_system_info,
            "search_knowledge_base": lambda q: tools.rag_search(q),
            "open_app": tools.open_app,
            "take_screenshot": tools.take_screenshot,
            "get_weather": lambda: tools.get_weather(),
        }

    def web_search(self, query: str) -> str:
        try:
            return tools.web_search(query)
        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return f"I couldn't perform a web search for '{query}' right now. The web search service might be unavailable."

    def browse_website(self, url: str) -> str:
        try:
            return tools.browse_website(url)
        except Exception as e:
            logger.error(f"Website browsing failed: {e}")
            return f"I couldn't open {url} right now. The website browsing service might be unavailable."

    def get_system_info(self) -> str:
        try:
            return tools.get_system_info()
        except Exception as e:
            logger.error(f"System info retrieval failed: {e}")
            return "I couldn't retrieve system information right now. The system monitoring service might be unavailable."

    def search_knowledge_base(self, query: str) -> str:
        try:
            return tools.rag_search(query)
        except Exception as e:
            logger.error(f"Knowledge base search failed: {e}")
            return f"I couldn't search the knowledge base for '{query}' right now. The RAG service might be unavailable."

    def open_app(self, app_name: str) -> str:
        try:
            return tools.open_app(app_name)
        except Exception as e:
            logger.error(f"App opening failed: {e}")
            return f"I couldn't open {app_name} right now. The application launcher service might be unavailable."

    def take_screenshot(self) -> str:
        try:
            return tools.take_screenshot()
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return "I couldn't take a screenshot right now. The screenshot service might be unavailable."

    def get_weather(self) -> str:
        try:
            return tools.get_weather()
        except Exception as e:
            logger.error(f"Weather retrieval failed: {e}")
            return "I couldn't get weather information right now. The weather service might be unavailable."

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
    if not text or not isinstance(text, str):
        return False
    if len(text.strip()) == 0:
        return False
    if len(text) > max_length:
        return False
    return True

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
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
            try:
                if sf is not None:
                    sf.write(f.name, wav_f32, SAMPLE_RATE, subtype="PCM_16")
                else:
                    import wave, struct
                    with wave.open(f.name, 'wb') as wf:
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

        # call whisper.cpp
        # -nt = no timestamps, -l language
        # -otxt = output to text file
        cmd = [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", tmp, "-l", WHISPER_LANG, "-nt", "-otxt", "-of", tmp]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            logger.error(f"Whisper transcription failed: {proc.stderr}")
            return ""
        try:
            with open(tmp + ".txt", "r") as rf:
                text = rf.read().strip()
        except FileNotFoundError:
            logger.error("Whisper output file not found")
            text = ""
        finally:
            try:
                os.unlink(tmp)
                os.unlink(tmp + ".txt")
            except OSError:
                pass
        return text
    except subprocess.TimeoutExpired:
        logger.error("Whisper transcription timed out")
        return ""
    except Exception as e:  # pragma: no cover
        logger.error(f"Transcription error: {e}")
        return ""


def transcribe(wav_f32: np.ndarray) -> str:
    """Transcribe audio using in-memory pipeline with graceful degradation."""
    if _WHISPER_IMPL == "whispercpp" and _WHISPER_CTX is not None:
        try:
            return _WHISPER_CTX.transcribe(wav_f32).strip()
        except Exception as e:  # pragma: no cover
            logger.error(f"whispercpp transcription failed: {e}")
    elif _WHISPER_IMPL == "whisper" and _WHISPER_CTX is not None:
        try:
            result = _WHISPER_CTX.transcribe(wav_f32, language=WHISPER_LANG)
            return result.get("text", "").strip()
        except Exception as e:  # pragma: no cover
            logger.error(f"whisper transcription failed: {e}")
    # Fallback to CLI
    return _transcribe_cli(wav_f32)


class StreamingTranscriber:
    """Optimized streaming transcriber with intelligent buffering and caching."""

    def __init__(self, max_buffer_duration: float = 30.0, sample_rate: int = 16000) -> None:
        self._buffer: np.ndarray = np.array([], dtype=np.float32)
        self._last_text: str = ""
        self._max_buffer_samples = int(max_buffer_duration * sample_rate)
        self._sample_rate = sample_rate
        
        # Optimization: cache for recent transcriptions to avoid redundant processing
        self._transcription_cache = {}
        self._cache_size = CFG.get_transcription_cache_size()
        self._min_chunk_size = int(CFG.get_min_chunk_duration() * sample_rate)
        
        # Performance tracking
        self._transcription_count = 0
        self._last_transcription_time = 0
        self._transcription_interval = CFG.get_transcription_interval()

    def add_chunk(self, chunk: np.ndarray) -> str:
        # Add new chunk to buffer
        self._buffer = np.concatenate([self._buffer, chunk])
        
        # Prevent memory leak by limiting buffer size
        if len(self._buffer) > self._max_buffer_samples:
            self._buffer = self._buffer[-self._max_buffer_samples:]
            logger.debug(f"Audio buffer trimmed to {self._max_buffer_samples} samples")
        
        # Only transcribe if we have enough audio and enough time has passed
        current_time = time.time()
        if (len(self._buffer) >= self._min_chunk_size and 
            current_time - self._last_transcription_time > self._transcription_interval):
            
            # Check cache first
            buffer_hash = hash(self._buffer.tobytes())
            if buffer_hash in self._transcription_cache:
                text = self._transcription_cache[buffer_hash]
            else:
                text = transcribe(self._buffer)
                # Cache the result
                if len(self._transcription_cache) >= self._cache_size:
                    # Remove oldest entry
                    oldest_key = next(iter(self._transcription_cache))
                    del self._transcription_cache[oldest_key]
                self._transcription_cache[buffer_hash] = text
                self._transcription_count += 1
                self._last_transcription_time = current_time
            
            delta = text[len(self._last_text) :]
            self._last_text = text
            return delta.strip()
        
        return ""

    def flush(self) -> str:
        text = self._last_text.strip()
        self._buffer = np.array([], dtype=np.float32)
        self._last_text = ""
        self._transcription_cache.clear()  # Clear cache on flush
        return text

# ---- Enhanced LLM chat with tool calling ----
TTS_STREAMED = False  # set true when llama_chat performs streaming TTS

def llama_chat(user_text: str) -> str:
    # Check if user is requesting tool usage
    if CFG.tools_enabled() and tool_caller:
        # Enhanced keyword-based tool detection
        user_text_lower = user_text.lower()
        
        # Web search
        if "search" in user_text_lower and ("web" in user_text_lower or "for" in user_text_lower):
            query = user_text_lower.replace("search", "").replace("for", "").replace("web", "").strip()
            result = tool_caller.web_search(query)
            return f"I searched for '{query}'. {result}"
        
        # Website browsing
        elif "browse" in user_text_lower or "website" in user_text_lower or "open website" in user_text_lower:
            words = user_text.split()
            for word in words:
                if word.startswith(("http://", "https://", "www.")):
                    result = tool_caller.browse_website(word)
                    return f"I browsed {word}. {result}"
        
        # App opening
        elif "open" in user_text_lower and "app" in user_text_lower:
            app_name = user_text_lower.replace("open", "").replace("app", "").strip()
            result = tool_caller.open_app(app_name)
            return result
        
        # Screenshot
        elif "screenshot" in user_text_lower or "take picture" in user_text_lower:
            result = tool_caller.take_screenshot()
            return result
        
        # Weather
        elif "weather" in user_text_lower:
            result = tool_caller.get_weather()
            return result
        
        # System info
        elif "system" in user_text_lower and "info" in user_text_lower:
            result = tool_caller.get_system_info()
            return f"Here's your system information: {result}"
        
        # RAG search
        elif any(keyword in user_text_lower for keyword in ["knowledge", "document", "file", "kb", "search kb", "search knowledge"]):
            result = tool_caller.search_knowledge_base(user_text)
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
                            threading.Thread(target=lambda: tts_manager.speak(to_say, interruptible=True, notify=False), daemon=True).start()

            if tts_buf.strip():
                TTS_STREAMED = True
                threading.Thread(target=lambda: tts_manager.speak(tts_buf.strip(), interruptible=True, notify=False), daemon=True).start()

            if INTERRUPTION_ENABLED and conversation_manager and not (tts_manager.audio_handler and tts_manager.audio_handler.interrupt_requested):
                conversation_manager.update_response(full_response, is_complete=True)
                try:
                    conversation_manager.complete_response()
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
class TTSManager:
    """Unified TTS manager handling different engines and interruption"""

    def __init__(self):
        self.engine = None
        self.engine_type = None
        self.audio_handler = None
        self.voices = []  # available voice names/ids for the active engine
        self.kokoro_available = False
        
        # Performance monitoring
        self.performance_stats = {
            'total_requests': 0,
            'total_duration': 0.0,
            'avg_duration': 0.0,
            'max_duration': 0.0,
            'min_duration': float('inf'),
            'errors': 0,
            'last_request_time': 0.0
        }
        self.performance_lock = threading.Lock()
        self.pyttsx3_available = False
        self.piper_available = False
        self.say_available = False
        self._speak_lock = threading.Lock()
        self._initialized = False  # lazy init to avoid crashes on import
        
        # TTS queue system for resource management
        self._tts_queue = queue.Queue(maxsize=MAX_CONCURRENT_TTS)
        self._active_tts_count = 0
        self._tts_count_lock = threading.Lock()
        
        # Async TTS processing
        self._tts_thread_pool = []
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
            return

        # Only Piper is supported now; no other engines to probe
        self.say_available = False

    def init_engine(self):
        if self._initialized:
            return
        self._initialized = True
        # Piper-only engine
        try:
            from . import config as _C
            import piper  # noqa: F401
            from piper import PiperVoice
            
            # Use quantized model as primary, fallback to original
            voice_path = _C.get_piper_voice_path()
            fallback_path = _C.get_piper_fallback_path()
            
            if os.path.exists(voice_path):
                # Use quantized model (now primary)
                self.engine = PiperVoice.load(voice_path)
                self.engine_type = "piper_quantized"
                self.piper_available = True
                print(f"✅ Piper quantized ready: {voice_path} (70% smaller, 2-3x faster)")
            elif fallback_path and os.path.exists(fallback_path):
                # Fallback to original model
                self.engine = PiperVoice.load(fallback_path)
                self.engine_type = "piper"
                self.piper_available = True
                print(f"✅ Piper fallback ready: {fallback_path} (original model)")
            else:
                raise ImportError(f"Piper voice model not found at {voice_path} or {fallback_path}")
        except Exception as e:
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

    def speak(self, text: str, interruptible: bool = False, notify: bool = True) -> bool:
        """Speak text using the configured TTS engine

        Args:
            text: Text to speak
            interruptible: Whether speech should support interruption

        Returns:
            bool: True if speech completed, False if interrupted
        """
        if not text.strip():
            return True
        
        # Log all TTS requests for debugging
        logger.info(f"TTS request: '{text[:50]}{'...' if len(text) > 50 else ''}'")

        # Try TTS with retry mechanism
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self._speak_attempt(text, interruptible, notify)
            except Exception as e:
                logger.warning(f"TTS attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    # Try to reinitialize engine on failure
                    try:
                        self._initialized = False
                        self.init_engine()
                        time.sleep(0.5)  # Brief delay before retry
                    except Exception as reinit_error:
                        logger.error(f"Failed to reinitialize TTS engine: {reinit_error}")
                else:
                    logger.error(f"All TTS attempts failed, falling back to system TTS")
                    return self._fallback_speak(text, notify)

    def _speak_attempt(self, text: str, interruptible: bool, notify: bool) -> bool:
        """Single TTS attempt with proper error handling"""
        if not self._initialized:
            self.init_engine()
        if not self.engine:
            raise RuntimeError("TTS engine not loaded")
        
        if notify:
            _notify_dashboard_state('speaking_started')

        if self.engine_type == "piper":
            return self._speak_with_piper(text, interruptible, notify)
        else:
            raise RuntimeError("No TTS engine available")

    def _speak_with_piper(self, text: str, interruptible: bool, notify: bool) -> bool:
        """Speak using Piper TTS with error recovery and caching"""
        try:
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
            config.noise_w = 0.6      # Reduced for faster synthesis
            config.phoneme_silence_sec = 0.05  # Reduced silence for faster output
            
            # Synthesize audio (returns generator)
            audio_chunks = self.engine.synthesize(text, config)
            
            # Process audio chunks from generator
            audio_arrays = []
            for ch in audio_chunks:
                try:
                    # Convert chunk to numpy array
                    if hasattr(ch, 'audio_float_array'):
                        audio_arrays.append(ch.audio_float_array)
                    elif hasattr(ch, 'audio'):
                        audio_arrays.append(np.array(ch.audio, dtype=np.float32))
                    else:
                        # Try to convert directly
                        audio_arrays.append(np.array(ch, dtype=np.float32))
                except Exception as e:
                    logger.warning(f"Failed to process audio chunk: {e}")
                    continue
            
            if not audio_arrays:
                logger.warning("No audio generated from Piper")
                if notify:
                    _notify_dashboard_state('speaking_ended')
                return False
            
            # Concatenate all audio arrays
            audio_arr = np.concatenate(audio_arrays).astype(np.float32)
            
            # Cache the audio for future use
            self._cache_audio(text, audio_arr)
            
            # Play audio
            if self.audio_handler and interruptible:
                audio_arr = self._ensure_rate(audio_arr, sr, TTS_SAMPLE_RATE)
                ok = self.audio_handler.play_audio(audio_arr)
                if notify:
                    _notify_dashboard_state('speaking_ended' if ok else 'speaking_interrupted')
                return ok
            else:
                return self._play_audio_sounddevice(audio_arr, sr, notify)
                
        except ImportError as e:
            logger.error(f"Piper import failed: {e}")
            raise RuntimeError("Piper TTS not available")
        except Exception as e:
            logger.error(f"Piper synthesis failed: {e}")
            raise

    def _play_audio_sounddevice(self, audio_arr: np.ndarray, sample_rate: int, notify: bool) -> bool:
        """Play audio using sounddevice with error recovery"""
        try:
            import sounddevice as sd
            sd.play(audio_arr, samplerate=sample_rate)
            sd.wait()
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
    
    def _log_performance(self, duration: float, success: bool = True):
        """Log TTS performance metrics"""
        with self.performance_lock:
            self.performance_stats['total_requests'] += 1
            self.performance_stats['last_request_time'] = time.time()
            
            if success:
                self.performance_stats['total_duration'] += duration
                self.performance_stats['max_duration'] = max(self.performance_stats['max_duration'], duration)
                self.performance_stats['min_duration'] = min(self.performance_stats['min_duration'], duration)
                self.performance_stats['avg_duration'] = self.performance_stats['total_duration'] / self.performance_stats['total_requests']
            else:
                self.performance_stats['errors'] += 1
    
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
        """Cache audio for text"""
        if not self._cache_enabled:
            return
        with self._cache_lock:
            # Simple LRU: remove oldest if cache is full
            if len(self._tts_cache) >= self._cache_max_size:
                # Remove first item (oldest)
                oldest_key = next(iter(self._tts_cache))
                del self._tts_cache[oldest_key]
            
            self._tts_cache[text] = audio.copy()
    
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
        logger.info(f"🎤 TTS Request: '{text[:50]}{'...' if len(text) > 50 else ''}' (length: {len(text)} chars)")
        
        if INTERRUPTION_ENABLED and conversation_manager:
            # Start conversation response tracking
            conversation_manager.start_response(text)

            # Use interruptible TTS
            _notify_dashboard_state('speaking_started')
            completed = tts_manager.speak(text, interruptible=True, notify=False)

            if completed:
                conversation_manager.update_response(text, is_complete=True)
                _notify_dashboard_state('speaking_ended')
                success = True
            else:
                # TTS was interrupted - only interrupt if not already interrupted
                with conversation_manager.lock:
                    if (conversation_manager.current_context and
                        conversation_manager.current_context.current_state != ConversationState.INTERRUPTED):
                        conversation_manager.interrupt_response()
                _notify_dashboard_state('speaking_interrupted')

        else:
            # Use non-interruptible TTS
            _notify_dashboard_state('speaking_started')
            completed = tts_manager.speak(text, interruptible=False, notify=False)
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
        tts_manager._log_performance(duration, success)
        
        # Log performance stats every 10 requests
        if tts_manager.performance_stats['total_requests'] % 10 == 0:
            stats = tts_manager.get_performance_stats()
            logger.info(f"📊 TTS Performance: Avg={stats['avg_duration']:.2f}s, "
                       f"Max={stats['max_duration']:.2f}s, "
                       f"CPU={stats['cpu_percent']:.1f}%, "
                       f"RAM={stats['memory_mb']:.1f}MB, "
                       f"Errors={stats['errors']}")
        
        logger.info(f"⏱️ TTS Duration: {duration:.2f}s ({'✅' if success else '❌'})")

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
        def _control_speak():
            try:
                data = request.get_json() or {}
                text = str(data.get('text', '')).strip()
                logger.info(f"TTS request received: '{text[:50]}{'...' if len(text) > 50 else ''}'")
                if not text:
                    return jsonify({'ok': False, 'error': 'text required'}), 400
                # ensure TTS is ready
                if tts_manager.engine is None or tts_manager.engine_type != 'piper':
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

        @control_app.route('/preview-voice', methods=['POST'])
        def _control_preview_voice():
            try:
                data = request.get_json() or {}
                text = str(data.get('text') or 'Hey there, how can I help?')
                threading.Thread(target=lambda: tts_manager.speak(text, interruptible=False, notify=False), daemon=True).start()
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
                engine_loaded = (tts_manager.engine_type == 'piper' and tts_manager.engine is not None)

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

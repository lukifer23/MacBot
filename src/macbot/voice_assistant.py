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
import sounddevice as sd
import soundfile as sf
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
    return np.sqrt(np.mean(block**2)) > thresh

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
    audio_q.put(indata.copy())

    if (
        INTERRUPTION_ENABLED
        and tts_manager.audio_handler
        and conversation_manager
        and conversation_manager.current_context
        and conversation_manager.current_context.current_state
        == ConversationState.SPEAKING
    ):
        if tts_manager.audio_handler.check_voice_activity(indata.reshape(-1)):
            conversation_manager.interrupt_response()

# ---- Whisper transcription ----
def _transcribe_cli(wav_f32: np.ndarray) -> str:
    """Fallback transcription via whisper.cpp CLI using temp files."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, wav_f32, SAMPLE_RATE, subtype="PCM_16")
            tmp = f.name

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
    """Maintain streaming state for real-time transcription."""

    def __init__(self) -> None:
        self._buffer: np.ndarray = np.array([], dtype=np.float32)
        self._last_text: str = ""

    def add_chunk(self, chunk: np.ndarray) -> str:
        self._buffer = np.concatenate([self._buffer, chunk])
        text = transcribe(self._buffer)
        delta = text[len(self._last_text) :]
        self._last_text = text
        return delta.strip()

    def flush(self) -> str:
        text = self._last_text.strip()
        self._buffer = np.array([], dtype=np.float32)
        self._last_text = ""
        return text

# ---- Enhanced LLM chat with tool calling ----
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
        with requests.post(
            LLAMA_SERVER,
            json=payload,
            stream=True,
            timeout=LLM_TIMEOUT,
        ) as r:
            r.raise_for_status()

            full_response = ""
            spoken_len = 0

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

                    new_text = full_response[spoken_len:]
                    spoken_len = len(full_response)

                    if new_text.strip():
                        # Use the unified TTS manager for streaming speech
                        def _speak_chunk(txt=new_text):
                            try:
                                tts_manager.speak(txt, interruptible=True)
                            except Exception as e:
                                print(f"TTS Error: {e}")

                        threading.Thread(target=_speak_chunk, daemon=True).start()

            if INTERRUPTION_ENABLED and conversation_manager and not (tts_manager.audio_handler and tts_manager.audio_handler.interrupt_requested):
                conversation_manager.update_response(full_response, is_complete=True)

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

        if os.environ.get("MACBOT_DISABLE_TTS") == "1":
            print("⚠️ TTS disabled via environment variable")
            return

        # Try Kokoro first (supports interruption)
        try:
            from kokoro import KPipeline
            self.engine = KPipeline(lang_code="a")  # American English
            self.engine_type = "kokoro"
            print("✅ Using Kokoro for TTS (interruptible)")

            # Initialize audio handler for interruption
            if INTERRUPTION_ENABLED:
                # Use shared audio handler to avoid duplicate streams
                try:
                    from .audio_interrupt import get_audio_handler
                    self.audio_handler = get_audio_handler()
                except Exception:
                    self.audio_handler = AudioInterruptHandler(sample_rate=TTS_SAMPLE_RATE)
                if hasattr(self.audio_handler, 'vad_threshold'):
                    self.audio_handler.vad_threshold = INTERRUPT_THRESHOLD

        except ImportError:
            print("⚠️ Kokoro not available, trying pyttsx3...")
            try:
                import pyttsx3
                self.engine = pyttsx3.init()
                self.engine.setProperty("rate", int(SPEED * TTS_RATE_MULTIPLIER))
                self.engine_type = "pyttsx3"
                print("✅ Using pyttsx3 for TTS (non-interruptible)")
            except ImportError:
                print("❌ No TTS engine available")
                self.engine = None
                self.engine_type = None

    def speak(self, text: str, interruptible: bool = False) -> bool:
        """Speak text using the configured TTS engine

        Args:
            text: Text to speak
            interruptible: Whether speech should support interruption

        Returns:
            bool: True if speech completed, False if interrupted
        """
        if not self.engine or not text.strip():
            return True

        try:
            if self.engine_type == "kokoro" and self.audio_handler and interruptible:
                # Use interruptible TTS with Kokoro
                audio = self.engine(text)
                if isinstance(audio, tuple):
                    audio_data = audio[0]
                else:
                    audio_data = audio

                # Ensure numpy array
                if not isinstance(audio_data, np.ndarray):
                    audio_data = np.array(audio_data)

                return self.audio_handler.play_audio(audio_data)

            elif self.engine_type == "pyttsx3":
                # Use non-interruptible pyttsx3
                self.engine.say(text)
                self.engine.runAndWait()
                return True

            else:
                # Fallback for any other engine
                print(f"⚠️ Unsupported TTS engine: {self.engine_type}")
                return True

        except Exception as e:
            print(f"TTS Error: {e}")
            return True

    def interrupt(self):
        """Interrupt current speech"""
        if self.audio_handler:
            self.audio_handler.interrupt_playback()
            logger.info("TTS playback interrupted")

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
    """Speak text using the unified TTS system"""
    if INTERRUPTION_ENABLED and conversation_manager:
        # Start conversation response tracking
        conversation_manager.start_response(text)

        # Use interruptible TTS
        _notify_dashboard_state('speaking_started')
        completed = tts_manager.speak(text, interruptible=True)

        if completed:
            conversation_manager.update_response(text, is_complete=True)
            _notify_dashboard_state('speaking_ended')
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
        tts_manager.speak(text, interruptible=False)
        _notify_dashboard_state('speaking_ended')

"""Web GUI removed from voice_assistant; use web_dashboard service instead."""

# ---- Main loop ----
def main():
    print("🚀 Starting MacBot Voice Assistant...")
    print("Local Voice AI ready. Speak after the beep. (Ctrl+C to quit)")
    print("💡 Try saying:")
    print("   • 'search for weather' - Web search")
    print("   • 'browse example.com' - Open website")
    print("   • 'open app safari' - Launch applications")
    print("   • 'take screenshot' - Capture screen")
    print("   • 'system info' - System status")
    print("🌐 Tip: Start the web dashboard via 'macbot-dashboard' for UI.")

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

        @control_app.route('/mic-check', methods=['POST'])
        def _control_mic_check():
            """Attempt to open a short-lived input stream to trigger OS mic permission.
            Returns JSON with success/error for guidance."""
            try:
                # Try to open and immediately close a short input stream
                with sd.InputStream(channels=1, samplerate=SAMPLE_RATE, dtype='float32'):
                    pass
                return jsonify({'ok': True})
            except Exception as e:
                logger.warning(f"Mic check failed: {e}")
                return jsonify({'ok': False, 'error': str(e)}), 500

        def _run_control():
            try:
                control_app.run(host=VA_HOST, port=VA_PORT, debug=False, use_reloader=False)
            except Exception as e:
                logger.warning(f"Voice assistant control server failed to start: {e}")

        threading.Thread(target=_run_control, daemon=True).start()
        logger.info(f"Voice assistant control server on http://{VA_HOST}:{VA_PORT}")
    except Exception as e:
        logger.warning(f"Voice assistant control server not started: {e}")

    # Initialize audio input; run in text-only mode if unavailable
    stream = None
    try:
        sd.play(np.zeros(1200), samplerate=TTS_SAMPLE_RATE, blocking=True)
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
    stream_tr = StreamingTranscriber()
    last_voice = time.time()

    try:
        while True:
            if stream is None:
                time.sleep(1.0)
                continue
            block = audio_q.get()
            block = block.reshape(-1)
            v = is_voiced(block)
            now = time.time()

            if v:
                voiced = True
                last_voice = now
                delta = stream_tr.add_chunk(block)
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
    main()

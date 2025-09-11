import os
import queue
import subprocess
import json
import tempfile
import time
import threading
import sys
import signal
import numpy as np
import sounddevice as sd
import soundfile as sf
import requests
import psutil
import logging
from typing import Dict, List, Optional
from pathlib import Path

from .audio_interrupt import AudioInterruptHandler
from .conversation_manager import (
    ConversationManager,
    ConversationContext,
    ConversationState,
)
from . import config as CFG
from . import tools

# Configure logging
logger = logging.getLogger(__name__)

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
        and audio_handler
        and conversation_manager
        and conversation_manager.current_context
        and conversation_manager.current_context.current_state
        == ConversationState.SPEAKING
    ):
        if audio_handler.check_voice_activity(indata.reshape(-1)):
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
                if audio_handler and audio_handler.interrupt_requested:
                    if INTERRUPTION_ENABLED and conversation_manager:
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
                        def _speak_chunk(txt=new_text):
                            try:
                                if HAS_KOKORO and tts is not None and audio_handler:
                                    audio = tts(txt)
                                    if isinstance(audio, tuple):
                                        audio_data = audio[0]
                                    else:
                                        audio_data = audio
                                    
                                    # Ensure audio_data is a numpy array
                                    if not isinstance(audio_data, np.ndarray):
                                        audio_data = np.array(audio_data)
                                    
                                    audio_handler.play_audio(audio_data)  # type: ignore
                                elif 'tts_engine' in globals():
                                    tts_engine.say(txt)
                                    tts_engine.runAndWait()
                            except Exception as e:
                                print(f"TTS Error: {e}")

                        threading.Thread(target=_speak_chunk, daemon=True).start()

            if INTERRUPTION_ENABLED and conversation_manager and not (audio_handler and audio_handler.interrupt_requested):
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
# Use pyttsx3 as a more compatible TTS engine. Allow disabling via env for headless tasks.
if os.environ.get("MACBOT_DISABLE_TTS") == "1":
    tts_engine = None
    HAS_KOKORO = False
else:  # pragma: no cover - optional runtime feature
    try:
        import pyttsx3  # type: ignore
        tts_engine = pyttsx3.init()
        tts_engine.setProperty("rate", int(SPEED * TTS_RATE_MULTIPLIER))  # Adjust rate for pyttsx3
        HAS_KOKORO = False
        print("✅ Using pyttsx3 for TTS")
    except ImportError:
        print("⚠️  pyttsx3 not available, trying kokoro...")
        try:
            from kokoro import KPipeline
            tts = KPipeline(lang_code="a")  # American English
            HAS_KOKORO = True
            print("✅ Using Kokoro for TTS")
        except ImportError:
            print("❌ No TTS engine available")
            HAS_KOKORO = False
            tts = None

# ---- Interruptible Conversation System ----
if INTERRUPTION_ENABLED:
    audio_handler = AudioInterruptHandler(
        sample_rate=TTS_SAMPLE_RATE
    )
    # Set VAD threshold if available
    if hasattr(audio_handler, 'vad_threshold'):
        audio_handler.vad_threshold = INTERRUPT_THRESHOLD
    conversation_manager = ConversationManager(
        max_history=CONTEXT_BUFFER_SIZE,
        context_timeout=CONVERSATION_TIMEOUT
    )

    # Register conversation state callback for audio interruption
    def on_conversation_state_change(context: ConversationContext):
        """Handle conversation state changes"""
        if context.current_state == ConversationState.INTERRUPTED and audio_handler:
            audio_handler.interrupt_playback()
            try:
                if 'tts_engine' in globals():
                    tts_engine.stop()
            except Exception:
                pass
            print("🎤 Conversation interrupted by user")

    conversation_manager.register_state_callback(on_conversation_state_change)
else:
    # Fallback for when interruption is disabled
    audio_handler = None
    conversation_manager = None

def speak(text: str):
    """Speak text using interruptible TTS system"""
    if INTERRUPTION_ENABLED and audio_handler and conversation_manager:
        def tts_worker(full_text: str):
            try:
                conversation_manager.start_response(full_text)

                # Generate TTS audio into a temporary wav file
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp_path = f.name
                try:
                    tts_engine.save_to_file(full_text, tmp_path)
                    tts_engine.runAndWait()
                    audio, sr = sf.read(tmp_path, dtype="float32")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

                chunk_size = int(sr * 0.25)  # 250ms chunks
                total_samples = len(audio)
                idx = 0

                while idx < total_samples:
                    if (
                        conversation_manager.current_context
                        and conversation_manager.current_context.current_state
                        == ConversationState.INTERRUPTED
                    ):
                        # Buffer remaining text for resume
                        remaining_ratio = idx / total_samples
                        remaining_index = int(len(full_text) * remaining_ratio)
                        with conversation_manager.lock:
                            ctx = conversation_manager.current_context
                            if ctx:
                                ctx.buffered_response = full_text[remaining_index:]
                                ctx.ai_response = full_text[:remaining_index]
                        return

                    chunk = audio[idx : idx + chunk_size]
                    audio_handler.play_audio(chunk)
                    idx += len(chunk)

                    spoken_chars = int(len(full_text) * idx / total_samples)
                    conversation_manager.update_response(full_text[:spoken_chars])

                conversation_manager.update_response(full_text)
                conversation_manager.complete_response()

            except Exception as e:
                logger.error(f"Interruptible TTS Error: {e}")

        threading.Thread(target=tts_worker, args=(text,), daemon=True).start()

    else:
        # Original blocking TTS when interruption is disabled
        try:
            tts_engine.say(text)
            tts_engine.runAndWait()
        except Exception as e:
            print(f"TTS Error: {e}")

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

    sd.play(np.zeros(1200), samplerate=TTS_SAMPLE_RATE, blocking=True)

    stream = sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        dtype="float32",
        blocksize=int(SAMPLE_RATE * BLOCK_DUR),
        callback=_callback,
    )
    stream.start()

    voiced = False
    stream_tr = StreamingTranscriber()
    last_voice = time.time()

    try:
        while True:
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
                            speak(reply)
                else:
                    if now - last_voice > SILENCE_HANG:
                        transcript = stream_tr.flush()
                        voiced = False
                        if transcript:
                            print(f"\n[YOU] {transcript}")
                            
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
                            speak(reply)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        stream.stop()
        stream.close()

if __name__ == "__main__":
    main()

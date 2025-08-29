import os, queue, subprocess, json, tempfile, time, threading, sys, signal
import numpy as np
import sounddevice as sd
import soundfile as sf
import requests
import yaml
import psutil
import webbrowser
from typing import Dict, List, Optional
from pathlib import Path
import chromadb
from sentence_transformers import SentenceTransformer

# Import interruptible conversation system
from audio_interrupt import AudioInterruptHandler
from conversation_manager import (
    ConversationManager,
    ConversationContext,
    ConversationState,
)

# ---- Load config ----
CFG_PATH = os.path.abspath("config.yaml")
if os.path.exists(CFG_PATH):
    with open(CFG_PATH, "r") as f:
        CFG = yaml.safe_load(f)
else:
    CFG = {}

def _get(path, default=None):
    cur = CFG
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur

LLAMA_SERVER = _get("llama.server_url", "http://localhost:8080/v1/chat/completions")
LLAMA_TEMP   = float(_get("llama.temperature", 0.4))
LLAMA_MAXTOK = int(_get("llama.max_tokens", 200))

SYSTEM_PROMPT = _get("system_prompt", "You are a helpful AI assistant with access to tools. You can search the web, browse websites, and access your knowledge base. Always be concise and helpful.")

WHISPER_BIN   = os.path.abspath(_get("whisper.bin", "whisper.cpp/build/bin/whisper-cli"))
WHISPER_MODEL = os.path.abspath(_get("whisper.model", "whisper.cpp/models/ggml-base.en.bin"))
WHISPER_LANG  = _get("whisper.language", "en")

VOICE    = _get("tts.voice", "af_heart")
SPEED    = float(_get("tts.speed", 1.0))

SAMPLE_RATE   = int(_get("audio.sample_rate", 16000))
BLOCK_DUR     = float(_get("audio.block_sec", 0.03))
VAD_THRESH    = float(_get("audio.vad_threshold", 0.005))
SILENCE_HANG  = float(_get("audio.silence_hang", 0.6))

# Interruption settings
INTERRUPTION_ENABLED = _get("voice_assistant.interruption.enabled", True)
INTERRUPT_THRESHOLD = float(_get("voice_assistant.interruption.interrupt_threshold", 0.01))
INTERRUPT_COOLDOWN = float(_get("voice_assistant.interruption.interrupt_cooldown", 0.5))
CONVERSATION_TIMEOUT = int(_get("voice_assistant.interruption.conversation_timeout", 30))
CONTEXT_BUFFER_SIZE = int(_get("voice_assistant.interruption.context_buffer_size", 10))

# Tool calling and RAG settings
ENABLE_TOOLS = _get("tools.enabled", True)
ENABLE_RAG = _get("rag.enabled", True)
RAG_DB_PATH = _get("rag.db_path", "rag_database")

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
            "web_search": self.web_search,
            "browse_website": self.browse_website,
            "get_system_info": self.get_system_info,
            "search_knowledge_base": self.search_knowledge_base,
            "open_app": self.open_app,
            "take_screenshot": self.take_screenshot,
            "get_weather": self.get_weather
        }
    
    def web_search(self, query: str) -> str:
        """Search the web using macOS Safari"""
        try:
            # Use macOS Safari to perform the search
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            
            # Open Safari with the search query
            subprocess.run(['open', '-a', 'Safari', search_url], check=True)
            
            return f"I've opened Safari and searched for '{query}'. The results should be displayed in your browser."
        except Exception as e:
            return f"Web search failed: {str(e)}"
    
    def browse_website(self, url: str) -> str:
        """Open website in macOS Safari"""
        try:
            # Ensure URL has protocol
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            # Open Safari with the URL
            subprocess.run(['open', '-a', 'Safari', url], check=True)
            
            return f"I've opened {url} in Safari for you to browse."
        except Exception as e:
            return f"Website browsing failed: {str(e)}"
    
    def get_system_info(self) -> str:
        """Get current system information"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return f"System Status: CPU {cpu_percent}%, RAM {memory.percent}%, Disk {disk.percent}%"
        except Exception as e:
            return f"System info failed: {str(e)}"
    
    def search_knowledge_base(self, query: str) -> str:
        """Search the local knowledge base using RAG.

        Returns the top matching documents from the knowledge base or an
        error message if the RAG system is unavailable."""
        if not ENABLE_RAG:
            return "RAG is not enabled"
        if not rag_system:
            return "Knowledge base is unavailable"

        try:
            results = rag_system.search(query)
            if not results:
                return "No relevant information found in the knowledge base."
            formatted = "\n".join(f"{i+1}. {doc}" for i, doc in enumerate(results))
            return f"Top results:\n{formatted}"
        except Exception as e:
            return f"Knowledge base search failed: {str(e)}"
    
    def open_app(self, app_name: str) -> str:
        """Open a macOS application"""
        try:
            # Common app mappings
            app_mapping = {
                'safari': 'Safari',
                'chrome': 'Google Chrome',
                'finder': 'Finder',
                'terminal': 'Terminal',
                'mail': 'Mail',
                'messages': 'Messages',
                'facetime': 'FaceTime',
                'photos': 'Photos',
                'music': 'Music',
                'calendar': 'Calendar',
                'notes': 'Notes',
                'calculator': 'Calculator'
            }
            
            app_name_lower = app_name.lower()
            if app_name_lower in app_mapping:
                app_to_open = app_mapping[app_name_lower]
                subprocess.run(['open', '-a', app_to_open], check=True)
                return f"I've opened {app_to_open} for you."
            else:
                # Try to open the app directly
                subprocess.run(['open', '-a', app_name], check=True)
                return f"I've opened {app_name} for you."
                
        except Exception as e:
            return f"Failed to open {app_name}: {str(e)}"
    
    def take_screenshot(self) -> str:
        """Take a screenshot using macOS built-in tools"""
        try:
            # Use macOS screenshot tool
            timestamp = int(time.time())
            filename = f"screenshot_{timestamp}.png"
            filepath = os.path.expanduser(f"~/Desktop/{filename}")
            
            # Take screenshot of entire screen
            subprocess.run(['screencapture', filepath], check=True)
            
            return f"I've taken a screenshot and saved it to your Desktop as {filename}"
        except Exception as e:
            return f"Screenshot failed: {str(e)}"
    
    def get_weather(self) -> str:
        """Get weather using macOS Weather app"""
        try:
            # Open Weather app
            subprocess.run(['open', '-a', 'Weather'], check=True)
            return "I've opened the Weather app for you to check the current conditions."
        except Exception as e:
            return f"Weather app failed to open: {str(e)}"

# Initialize tool caller
tool_caller = ToolCaller() if ENABLE_TOOLS else None

# ---- RAG System ----
class RAGSystem:
    def __init__(self, db_path: str = "rag_database"):
        self.db_path = db_path
        self.client = None
        self.embedding_model = None
        self.collection = None
        self.initialize_rag()
    
    def initialize_rag(self):
        """Initialize the RAG system"""
        if not ENABLE_RAG:
            return
        
        try:
            # Initialize ChromaDB
            self.client = chromadb.PersistentClient(path=self.db_path)
            
            # Initialize embedding model
            self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            
            # Create or get collection
            self.collection = self.client.get_or_create_collection(
                name="knowledge_base",
                metadata={"hnsw:space": "cosine"}
            )
            
            print("✅ RAG system initialized")
        except Exception as e:
            print(f"❌ RAG initialization failed: {e}")
            self.client = None
    
    def add_document(self, text: str, metadata: dict = None) -> bool:
        """Add a document to the knowledge base"""
        if not self.collection:
            return False
        
        try:
            # Generate embedding
            embedding = self.embedding_model.encode(text).tolist()
            
            # Add to collection
            self.collection.add(
                embeddings=[embedding],
                documents=[text],
                metadatas=[metadata or {}],
                ids=[f"doc_{int(time.time())}"]
            )
            return True
        except Exception as e:
            print(f"Failed to add document: {e}")
            return False
    
    def search(self, query: str, n_results: int = 3) -> List[str]:
        """Search the knowledge base"""
        if not self.collection:
            return []
        
        try:
            # Generate query embedding
            query_embedding = self.embedding_model.encode(query).tolist()
            
            # Search collection
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results
            )
            
            return results['documents'][0] if results['documents'] else []
        except Exception as e:
            print(f"RAG search failed: {e}")
            return []

# Initialize RAG system
rag_system = RAGSystem(RAG_DB_PATH) if ENABLE_RAG else None

# ---- Simple energy VAD ----
def is_voiced(block, thresh=VAD_THRESH):
    return np.sqrt(np.mean(block**2)) > thresh

# ---- Audio I/O ----
audio_q = queue.Queue()

def _callback(indata, frames, time_info, status):
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
def transcribe(wav_f32: np.ndarray) -> str:
    # write temp wav
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, wav_f32, SAMPLE_RATE, subtype="PCM_16")
        tmp = f.name
    # call whisper.cpp
    # -nt = no timestamps, -l language
    # -of writes a sidecar .txt next to the input
    cmd = [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", tmp, "-l", WHISPER_LANG, "-nt", "-of", tmp]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("[whisper] error:", proc.stderr, file=sys.stderr)
        return ""
    try:
        with open(tmp + ".txt", "r") as rf:
            text = rf.read().strip()
    except FileNotFoundError:
        text = ""
    return text

# ---- Enhanced LLM chat with tool calling ----
def llama_chat(user_text: str) -> str:
    # Check if user is requesting tool usage
    if ENABLE_TOOLS and tool_caller:
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
        elif ENABLE_RAG and any(keyword in user_text_lower for keyword in ["knowledge", "document", "file"]):
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
            headers={"Authorization": "Bearer x"},
            json=payload,
            stream=True,
            timeout=120,
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
                                if HAS_KOKORO and 'tts' in globals() and audio_handler:
                                    audio = tts(txt)
                                    if isinstance(audio, tuple):
                                        audio_data = audio[0]
                                    else:
                                        audio_data = audio
                                    audio_handler.play_audio(audio_data)
                                elif 'tts_engine' in globals():
                                    tts_engine.say(txt)
                                    tts_engine.runAndWait()
                            except Exception as e:
                                print(f"TTS Error: {e}")

                        threading.Thread(target=_speak_chunk, daemon=True).start()

            if INTERRUPTION_ENABLED and conversation_manager and not (audio_handler and audio_handler.interrupt_requested):
                conversation_manager.update_response(full_response, is_complete=True)

            return full_response
    except Exception as e:
        return f"I'm having trouble connecting to the language model: {str(e)}"

# ---- TTS Setup ----
# Use pyttsx3 as a more compatible TTS engine
try:
    import pyttsx3
    tts_engine = pyttsx3.init()
    tts_engine.setProperty('rate', int(SPEED * 180))  # Adjust rate for pyttsx3
    HAS_KOKORO = False
    print("✅ Using pyttsx3 for TTS")
except ImportError:
    print("⚠️  pyttsx3 not available, trying kokoro...")
    try:
        from kokoro import KPipeline
        tts = KPipeline(lang_code='a')  # American English
        HAS_KOKORO = True
        print("✅ Using Kokoro for TTS")
    except ImportError:
        print("❌ No TTS engine available")
        HAS_KOKORO = False
        tts = None

# ---- Interruptible Conversation System ----
if INTERRUPTION_ENABLED:
    audio_handler = AudioInterruptHandler(
        sample_rate=24000,
        vad_threshold=INTERRUPT_THRESHOLD,
        interrupt_cooldown=INTERRUPT_COOLDOWN
    )
    conversation_manager = ConversationManager(
        max_history=CONTEXT_BUFFER_SIZE,
        context_timeout=CONVERSATION_TIMEOUT
    )

    # Register conversation state callback for audio interruption
    def on_conversation_state_change(context: ConversationContext):
        """Handle conversation state changes"""
        if context.current_state == ConversationState.INTERRUPTED:
            audio_handler.interrupt_playback()
            print("🎤 Conversation interrupted by user")

    conversation_manager.register_state_callback(on_conversation_state_change)
else:
    # Fallback for when interruption is disabled
    audio_handler = None
    conversation_manager = None

def speak(text: str):
    """Speak text using interruptible TTS system"""
    if INTERRUPTION_ENABLED and audio_handler and conversation_manager:
        try:
            conversation_manager.start_response(text)

            # Use pyttsx3 for TTS with interruption capability
            def on_start(name):
                pass

            def on_end(name):
                if (
                    conversation_manager.current_context
                    and conversation_manager.current_context.current_state
                    != ConversationState.INTERRUPTED
                ):
                    conversation_manager.complete_response()

            tts_engine.connect('started-utterance', on_start)
            tts_engine.connect('finished-utterance', on_end)

            tts_engine.say(text)
            tts_engine.runAndWait()

        except Exception as e:
            print(f"Interruptible TTS Error: {e}")
            # Fallback to original blocking method
            try:
                tts_engine.say(text)
                tts_engine.runAndWait()
            except Exception as e2:
                print(f"Fallback TTS also failed: {e2}")
    else:
        # Original blocking TTS when interruption is disabled
        try:
            tts_engine.say(text)
            tts_engine.runAndWait()
        except Exception as e:
            print(f"TTS Error: {e}")

# ---- Web GUI ----
def start_web_gui():
    """Start a simple web GUI for monitoring and interaction"""
    try:
        from flask import Flask, render_template_string, jsonify, request
        import threading
        
        app = Flask(__name__)
        
        # Global state for the web GUI
        gui_state = {
            'transcription': '',
            'response': '',
            'system_stats': {},
            'conversation_history': []
        }
        
        @app.route('/')
        def home():
            html = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>MacBot Dashboard</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
                    .container { max-width: 1200px; margin: 0 auto; }
                    .card { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
                    .stat { text-align: center; padding: 15px; background: #f8f9fa; border-radius: 6px; }
                    .stat-value { font-size: 24px; font-weight: bold; color: #007bff; }
                    .chat-area { height: 400px; overflow-y: auto; border: 1px solid #ddd; padding: 15px; background: #fafafa; }
                    .input-area { display: flex; gap: 10px; margin-top: 15px; }
                    input[type="text"] { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 4px; }
                    button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
                    button:hover { background: #0056b3; }
                    .refresh { background: #28a745; }
                    .refresh:hover { background: #1e7e34; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🤖 MacBot Voice Assistant Dashboard</h1>
                    
                    <div class="card">
                        <h2>📊 System Status</h2>
                        <div class="stats" id="stats">
                            <div class="stat">
                                <div class="stat-value" id="cpu">--</div>
                                <div>CPU Usage</div>
                            </div>
                            <div class="stat">
                                <div class="stat-value" id="memory">--</div>
                                <div>Memory Usage</div>
                            </div>
                            <div class="stat">
                                <div class="stat-value" id="disk">--</div>
                                <div>Disk Usage</div>
                            </div>
                            <div class="stat">
                                <div class="stat-value" id="model">Qwen3-4B</div>
                                <div>Active Model</div>
                            </div>
                        </div>
                        <button class="refresh" onclick="refreshStats()">🔄 Refresh Stats</button>
                    </div>
                    
                    <div class="card">
                        <h2>💬 Conversation</h2>
                        <div class="chat-area" id="chatArea">
                            <div><strong>MacBot:</strong> Hello! I'm ready to help. You can speak to me or type here.</div>
                        </div>
                        <div class="input-area">
                            <input type="text" id="userInput" placeholder="Type your message here..." onkeypress="handleKeyPress(event)">
                            <button onclick="sendMessage()">Send</button>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h2>🎤 Voice Input</h2>
                        <p>Current transcription: <span id="transcription">Waiting for voice input...</span></p>
                        <p>Last response: <span id="response">None yet</span></p>
                    </div>
                </div>
                
                <script>
                    function refreshStats() {
                        fetch('/api/stats')
                            .then(response => response.json())
                            .then(data => {
                                document.getElementById('cpu').textContent = data.cpu + '%';
                                document.getElementById('memory').textContent = data.memory + '%';
                                document.getElementById('disk').textContent = data.disk + '%';
                            });
                    }
                    
                    function sendMessage() {
                        const input = document.getElementById('userInput');
                        const message = input.value.trim();
                        if (!message) return;
                        
                        // Add user message to chat
                        addToChat('You', message);
                        input.value = '';
                        
                        // Send to backend
                        fetch('/api/chat', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({message: message})
                        })
                        .then(response => response.json())
                        .then(data => {
                            addToChat('MacBot', data.response);
                            document.getElementById('response').textContent = data.response;
                        });
                    }
                    
                    function handleKeyPress(event) {
                        if (event.key === 'Enter') {
                            sendMessage();
                        }
                    }
                    
                    function addToChat(speaker, message) {
                        const chatArea = document.getElementById('chatArea');
                        const div = document.createElement('div');
                        div.innerHTML = `<strong>${speaker}:</strong> ${message}`;
                        chatArea.appendChild(div);
                        chatArea.scrollTop = chatArea.scrollHeight;
                    }
                    
                    // Auto-refresh stats every 10 seconds
                    setInterval(refreshStats, 10000);
                    
                    // Initial load
                    refreshStats();
                </script>
            </body>
            </html>
            '''
            return html
        
        @app.route('/api/stats')
        def get_stats():
            try:
                cpu_percent = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                
                return jsonify({
                    'cpu': round(cpu_percent, 1),
                    'memory': round(memory.percent, 1),
                    'disk': round(disk.percent, 1)
                })
            except:
                return jsonify({'cpu': 0, 'memory': 0, 'disk': 0})
        
        @app.route('/api/chat', methods=['POST'])
        def chat():
            try:
                data = request.json
                user_message = data.get('message', '')
                
                # Get response from LLM
                response = llama_chat(user_message)
                
                # Update GUI state
                gui_state['conversation_history'].append({
                    'user': user_message,
                    'bot': response,
                    'timestamp': time.time()
                })
                
                return jsonify({'response': response})
            except Exception as e:
                return jsonify({'response': f'Error: {str(e)}'})
        
        @app.route('/api/update_transcription')
        def update_transcription():
            return jsonify({
                'transcription': gui_state['transcription'],
                'response': gui_state['response']
            })
        
        # Start Flask in a separate thread
        def run_flask():
            app.run(host='0.0.0.0', port=3000, debug=False)
        
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        print("🌐 Web GUI started at http://localhost:3000")
        return True
        
    except ImportError:
        print("❌ Flask not available, web GUI disabled")
        return False

# ---- Main loop ----
def main():
    print("🚀 Starting Enhanced MacBot Voice Assistant...")
    
    # Start web GUI if possible
    web_gui_started = start_web_gui()
    
    print("Local Voice AI ready. Speak after the beep. (Ctrl+C to quit)")
    print("💡 Try saying:")
    print("   • 'search for weather' - Web search")
    print("   • 'browse example.com' - Open website")
    print("   • 'open app safari' - Launch applications")
    print("   • 'take screenshot' - Capture screen")
    print("   • 'weather' - Check weather")
    print("   • 'system info' - System status")
    if web_gui_started:
        print("🌐 Web dashboard available at http://localhost:3000")
    
    sd.play(np.zeros(1200), samplerate=24000, blocking=True)

    stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE, dtype='float32', blocksize=int(SAMPLE_RATE*BLOCK_DUR), callback=_callback)
    stream.start()

    voiced = False
    seg = []
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
                seg.append(block)
            elif voiced:
                # candidate end-of-turn
                if HAS_TURN_DETECT:
                    # simple delay + confirm (for demo)
                    if now - last_voice > 0.35:
                        transcript = transcribe(np.concatenate(seg))
                        seg.clear(); voiced = False
                        if transcript:
                            print(f"\n[YOU] {transcript}")
                            if INTERRUPTION_ENABLED and conversation_manager:
                                conversation_manager.start_conversation()
                                conversation_manager.add_user_input(transcript)
                            reply = llama_chat(transcript)
                            print(f"[BOT] {reply}\n")
                            speak(reply)
                else:
                    if now - last_voice > SILENCE_HANG:
                        transcript = transcribe(np.concatenate(seg))
                        seg.clear(); voiced = False
                        if transcript:
                            print(f"\n[YOU] {transcript}")
                            if INTERRUPTION_ENABLED and conversation_manager:
                                conversation_manager.start_conversation()
                                conversation_manager.add_user_input(transcript)
                            reply = llama_chat(transcript)
                            print(f"[BOT] {reply}\n")
                            speak(reply)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        stream.stop()
        stream.close()

if __name__ == "__main__":
    main()

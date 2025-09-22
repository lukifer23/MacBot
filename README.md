# MacBot - Local AI Voice Assistant for macOS

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg)](https://www.docker.com/)

**MacBot** is a comprehensive offline AI voice assistant for macOS that runs entirely on your local machine. It features a complete 5-model pipeline with voice activity detection, speech-to-text, large language model processing, text-to-speech, and native macOS tool integration.

## Features

- **Advanced Offline Voice Pipeline**: VAD + Whisper Large v3 STT (Metal accelerated) + Neural TTS
- **High-Performance LLM**: Local inference with llama.cpp, optimized for Apple Silicon
- **Superior Text-to-Speech**: Piper neural TTS with 70% smaller models, 2-3x faster synthesis, intelligent caching, and hardware acceleration
- **Enterprise Security**: JWT authentication, input validation, XSS protection, and secure API access
- **Optimized Performance**: Metal GPU acceleration, ~0.2s STT latency, memory leak prevention, and intelligent caching
- **Enhanced macOS Integration**: Web search, screenshots, app launching, system monitoring
- **Modern Web Dashboard**: Real-time monitoring with WebSocket live updates and circuit breaker status
- **Advanced RAG System**: Document ingestion and semantic search with ChromaDB and API key authentication
- **Production-Ready**: Docker deployment with orchestrator, comprehensive health monitoring, and automatic recovery
- **Comprehensive Configuration**: YAML-based configuration with extensive customization and environment variable support
- **Smart Interruptibility**: Natural conversation flow with voice activity detection and barge-in capability
- **Real-Time Communication**: WebSocket bidirectional communication for live interaction
- **Performance Optimized**: Circuit breaker pattern, resource management, and backpressure handling
- **Production Ready**: Zero type checker errors, structured logging, and enterprise-grade reliability
- **TTS Performance**: 70% smaller models, 2-3x faster synthesis, MPS acceleration, and real-time monitoring

## Quick Start

### Prerequisites

```bash
# Install system dependencies
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install cmake ffmpeg portaudio python@3.11 git git-lfs
```

### 1. Clone and Setup

```bash
git clone https://github.com/lukifer23/MacBot.git
cd MacBot

# Initialize Git LFS for model files
git lfs install
git lfs track "*.gguf"
git lfs track "*.bin"

# Create virtual environment
python3.11 -m venv macbot_env
source macbot_env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install TTS engines (optional but recommended)
pip install piper-tts
# Download Piper voice model
mkdir -p piper_voices/en_US-lessac-medium
curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx" \
     -o piper_voices/en_US-lessac-medium/model.onnx
curl -L "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" \
     -o piper_voices/en_US-lessac-medium/model.onnx.json
```

### 2. Build Dependencies

```bash
# Build all dependencies (whisper.cpp, llama.cpp)
make build-all

# Or build individually
make build-whisper
make build-llama
```

### 3. Download Models

```bash
# Download Whisper Large v3 model (recommended for best accuracy)
cd models/whisper.cpp
sh ./models/download-ggml-model.sh large-v3-turbo-q5_0

# Download LLM model (GGUF format) to models/llama.cpp/models/
# Recommended: Qwen3-4B-Instruct-2507-Q4_K_M.gguf or similar
```

### 4. Configure

Edit `config/config.yaml` to customize settings:

```yaml
models:
  llm:
    path: "models/llama.cpp/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    context_length: 8192
    temperature: 0.4
  stt:
    model: "models/whisper.cpp/models/ggml-large-v3-turbo-q5_0.bin"
    language: "en"
  tts:
    voice: "en_US-lessac-medium"  # Piper voice
    speed: 1.0

tools:
  enabled:
    - web_search
    - screenshot
    - app_launcher
    - system_monitor
    - weather
    - rag_search
```

### 5. Run

```bash
# Start all services with orchestrator
python src/macbot/orchestrator.py

# Or use individual commands
make run-llama      # Start LLM server
make run-assistant  # Start voice assistant

# Or use CLI
python src/macbot/cli.py orchestrator

Note:
- The Voice Assistant now exposes a lightweight control server (default: http://localhost:8123) used by the Web Dashboard to send interruption requests and perform health checks.
- Ensure `ffmpeg` is installed for voice input from the browser (used to convert WebM/Opus to WAV for Whisper).
- Assistant UI states: The assistant now notifies the dashboard about speaking start/end/interrupt events so the banner shows Listening / Speaking / Interrupted / Ready in real time.
- Orchestrator API binds to http://127.0.0.1:8090 by default for safety; the Web Dashboard proxies common calls.

### Quick Verify

After starting, run the verification to check core endpoints:

```
make verify
# or
python scripts/verify_setup.py
```
```

## Docker Deployment

```bash
# Build and run with docker-compose
docker-compose up --build

# Or run individual services
docker-compose up macbot-orchestrator
```

## Documentation

- **[docs/ENHANCED_FEATURES.md](docs/ENHANCED_FEATURES.md)** - Comprehensive feature guide
- **[docs/API_REFERENCE.md](docs/API_REFERENCE.md)** - API endpoint documentation
- **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** - Detailed configuration guide
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** - Development setup and contribution guide

## Project Structure

```
MacBot/
├── src/macbot/              # Main package
│   ├── cli.py              # Command-line interface
│   ├── __init__.py         # Package initialization
│   ├── voice_assistant.py  # Voice assistant with interruption
│   ├── audio_interrupt.py  # TTS interruption handler
│   ├── conversation_manager.py # Conversation state management
│   ├── message_bus.py      # Real-time communication
│   ├── orchestrator.py     # Service orchestration
│   ├── web_dashboard.py    # Web interface
│   ├── rag_server.py      # RAG knowledge base
│   ├── auth.py             # JWT authentication system
│   ├── validation.py       # Input validation and sanitization
│   ├── resource_manager.py # Resource lifecycle management
│   ├── error_handler.py    # Centralized error handling
│   └── logging_utils.py    # Structured logging utilities
├── scripts/                # Shell scripts
│   ├── bootstrap_mac.sh   # Bootstrap script
│   └── start_macbot.sh    # Startup script
├── tests/                  # Test files
│   ├── test_interruptible_conversation.py
│   └── test_message_bus.py
├── config/                 # Configuration files
│   └── config.yaml        # Main configuration
├── docs/                   # Documentation
├── data/                   # Data directories
│   ├── rag_data/          # Knowledge base data
│   └── rag_database/      # Vector database
├── models/                 # Model directories
│   ├── llama.cpp/         # LLM inference engine
│   └── whisper.cpp/       # Speech recognition
├── logs/                   # Log files
│   └── macbot.log         # Application logs
├── requirements.txt        # Python dependencies
├── requirements-dev.txt    # Development dependencies
├── pyproject.toml          # Modern Python packaging
├── setup.py               # Legacy packaging
├── Makefile               # Build and run commands
├── docker-compose.yml     # Docker orchestration
├── Dockerfile            # Container definition
└── README.md
```

## Configuration

MacBot uses a comprehensive YAML configuration system. Key sections:

### Model Configuration
```yaml
models:
  llm:
    path: "llama.cpp/models/model.gguf"
    context_length: 4096
    threads: -1
  stt:
    model: "base.en"
    language: "en"
  tts:
    voice: "af_heart"
    speed: 1.0
```

### Tool Configuration
```yaml
tools:
  enabled:
    - web_search
    - screenshot
    - app_launcher
    - system_monitor
  web_search:
    default_engine: "google"
    timeout: 10

### Security Configuration
```yaml
# Authentication (set via environment variables for security)
auth:
  enabled: true
  jwt_secret: null  # Set MACBOT_JWT_SECRET environment variable
  token_expiry_hours: 24

# Input validation
validation:
  enabled: true
  max_text_length: 10000
  xss_protection: true

# Resource management
resource_management:
  enabled: true
  cleanup_interval: 300
```

### Service Configuration
```yaml
services:
  web_dashboard:
    host: "0.0.0.0"
    port: 3000
  rag_server:
    host: "localhost"
    port: 8001
    api_tokens: null  # Set MACBOT_RAG_API_TOKENS environment variable
  voice_assistant:
    host: "localhost"   # Control server host
    port: 8123           # Control server port
```
```

## Voice Commands

MacBot supports various voice commands:

- **"system info"** - Get system status
- **"take screenshot"** - Capture screen
- **"open app calculator"** - Launch applications
- **"search for weather"** - Web search
- **"browse github.com"** - Open websites
- **"what's the weather"** - Weather app

## Interruptible Conversations

MacBot features natural conversation flow with barge-in capability:

### How It Works
- **Real-time Interruption**: Speak while MacBot is responding to interrupt
- **Context Preservation**: Conversation history is maintained across interruptions
- **Seamless Flow**: Natural back-and-forth conversation without waiting for responses to complete

### Configuration
Configure interruption settings in `config.yaml`:

```yaml
interruption:
  enabled: true
  voice_threshold: 0.3
  cooldown_period: 1.0
  interruption_timeout: 5.0
  buffer_size: 100
```

### Usage
- Start speaking naturally during MacBot's responses
- The system will detect your voice and stop current speech
- Your new request will be processed immediately
- Previous conversation context is preserved

## Development

### Setup Development Environment

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Format code
black src/
isort src/
```

### Building from Source

```bash
# Install in development mode
pip install -e .

# Or build distribution
python -m build
```

## Docker Development

```bash
# Build development image
docker build -t macbot:dev .

# Run with development mounts
docker run -v $(pwd):/app -p 3000:3000 macbot:dev
```

## System Requirements

### Hardware
- **CPU**: Apple Silicon (M1/M2/M3) or Intel x64
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 5GB for models and dependencies
- **GPU**: Metal support (Apple Silicon)

### Software
- **macOS**: 12.0+ (Monterey or later)
- **Python**: 3.11+ (recommended; Apple Silicon optimized)
- **Git LFS**: For model file management
- **TTS Engines**: Piper (neural quality) or pyttsx3 (fallback)
- **STT Engine**: Whisper.cpp v1.7.6 with Metal acceleration

### Performance Specifications
- **STT Latency**: ~0.2 seconds (Whisper Large v3)
- **TTS Speed**: 178 WPM (Piper neural voices)
- **LLM Context**: 8192+ tokens (configurable)
- **GPU Acceleration**: Metal framework on Apple Silicon

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed contribution guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- **llama.cpp** - High-performance LLM inference engine
- **Whisper.cpp** - Optimized speech recognition with Metal acceleration
- **Piper TTS** - Modern neural text-to-speech with natural voice quality
- **Kokoro** - Advanced neural TTS framework (framework ready)
- **ChromaDB** - Vector database for RAG knowledge base
- **LiveKit** - Voice activity detection and real-time communication
- **ONNX Runtime** - Cross-platform ML inference acceleration
- **SYSTRAN** - FasterWhisper optimization research

## Support

- **Issues**: [GitHub Issues](https://github.com/lukifer23/MacBot/issues)
- **Discussions**: [GitHub Discussions](https://github.com/lukifer23/MacBot/discussions)
- **Documentation**: See [docs/](docs/) folder

---

**MacBot** - Your local AI assistant with the power of native macOS tools.

# MacBot - Local AI Voice Assistant for macOS

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg)](https://www.docker.com/)

**MacBot** is a comprehensive offline AI voice assistant for macOS that runs entirely on your local machine. It features a complete 5-model pipeline with voice activity detection, speech-to-text, large language model processing, text-to-speech, and native macOS tool integration.

## ✨ Features

- **🎤 Offline Voice Interface**: Complete voice pipeline with VAD, Whisper STT, and Kokoro TTS
- **🧠 Local LLM Support**: Run large language models locally with llama.cpp
- **🔧 Native macOS Tools**: Web search, screenshots, app launching, system monitoring
- **🌐 Web Dashboard**: Real-time monitoring and chat interface
- **📚 RAG Knowledge Base**: Document ingestion and semantic search
- **🐳 Docker Support**: Containerized deployment with docker-compose
- **⚙️ Comprehensive Configuration**: YAML-based configuration system

## 🚀 Quick Start

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
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
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
# Download Whisper model (auto-downloaded by Makefile)
# Download LLM model (GGUF format) to llama.cpp/models/
# Example: Qwen3-4B-Instruct-2507-Q4_K_M.gguf
```

### 4. Configure

Edit `config.yaml` to customize settings:

```yaml
models:
  llm:
    path: "llama.cpp/models/your-model.gguf"
  stt:
    model: "base.en"
  tts:
    voice: "af_heart"

tools:
  enabled:
    - web_search
    - screenshot
    - app_launcher
```

### 5. Run

```bash
# Start all services with orchestrator
python orchestrator.py

# Or use individual commands
make run-llama      # Start LLM server
make run-assistant  # Start voice assistant

# Or use CLI
macbot orchestrator
```

## 🐳 Docker Deployment

```bash
# Build and run with docker-compose
docker-compose up --build

# Or run individual services
docker-compose up macbot-orchestrator
```

## 📖 Documentation

- **[ENHANCED_FEATURES.md](ENHANCED_FEATURES.md)** - Comprehensive feature guide
- **[docs/API_REFERENCE.md](docs/API_REFERENCE.md)** - API endpoint documentation
- **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** - Detailed configuration guide
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** - Development setup and contribution guide

## 🏗️ Project Structure

```
MacBot/
├── src/macbot/              # Main package
│   ├── cli.py              # Command-line interface
│   └── __init__.py         # Package initialization
├── docs/                   # Documentation
├── llama.cpp/              # LLM inference engine
├── whisper.cpp/            # Speech recognition
├── rag_data/               # Knowledge base data
├── rag_database/           # Vector database
├── config.yaml             # Main configuration
├── requirements.txt        # Python dependencies
├── requirements-dev.txt    # Development dependencies
├── pyproject.toml          # Modern Python packaging
├── setup.py               # Legacy packaging
├── Makefile               # Build and run commands
├── docker-compose.yml     # Docker orchestration
├── Dockerfile            # Container definition
└── README.md
```

## ⚙️ Configuration

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
```

## 🎯 Voice Commands

MacBot supports various voice commands:

- **"system info"** - Get system status
- **"take screenshot"** - Capture screen
- **"open app calculator"** - Launch applications
- **"search for weather"** - Web search
- **"browse github.com"** - Open websites
- **"what's the weather"** - Weather app

## 🔧 Development

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

## 🐳 Docker Development

```bash
# Build development image
docker build -t macbot:dev .

# Run with development mounts
docker run -v $(pwd):/app -p 3000:3000 macbot:dev
```

## 📊 System Requirements

### Hardware
- **CPU**: Apple Silicon (M1/M2/M3) or Intel x64
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 5GB for models and dependencies
- **GPU**: Metal support (Apple Silicon)

### Software
- **macOS**: 12.0+ (Monterey or later)
- **Python**: 3.9+
- **Git LFS**: For model file management

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed contribution guidelines.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **llama.cpp** - Efficient LLM inference
- **Whisper.cpp** - Fast speech recognition
- **Kokoro** - High-quality text-to-speech
- **ChromaDB** - Vector database for RAG
- **LiveKit** - Voice activity detection

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/lukifer23/MacBot/issues)
- **Discussions**: [GitHub Discussions](https://github.com/lukifer23/MacBot/discussions)
- **Documentation**: See [docs/](docs/) folder

---

**MacBot** - Your local AI assistant with the power of native macOS tools! 🚀

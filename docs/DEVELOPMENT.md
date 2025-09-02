# MacBot Development Guide

## Overview
This guide covers setting up a development environment, contributing to MacBot, and understanding the codebase architecture.

## Development Setup

### Prerequisites
```bash
# Install system dependencies
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install cmake ffmpeg portaudio python@3.11 git git-lfs

# Clone the repository
git clone https://github.com/lukifer23/MacBot.git
cd MacBot

# Initialize submodules
git submodule update --init --recursive
```

### Python Environment
```bash
# Create virtual environment
make venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt
```

### Build Dependencies
```bash
# Build llama.cpp
make build-llama

# Build whisper.cpp
make build-whisper

# Build all dependencies
make build-all
```

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
│   ├── message_bus_client.py # Message bus client
│   ├── orchestrator.py     # Service orchestration
│   ├── web_dashboard.py    # Web interface
│   └── rag_server.py      # RAG knowledge base
├── scripts/                # Shell scripts
│   ├── bootstrap_mac.sh   # Bootstrap script
│   └── start_macbot.sh    # Startup script
├── tests/                  # Test files
│   ├── test_interruptible_conversation.py
│   ├── test_message_bus.py
│   └── test_message_bus_client.py
├── config/                 # Configuration files
│   └── config.yaml        # Main configuration
├── docs/                   # Documentation
│   ├── API_REFERENCE.md
│   ├── CONFIGURATION.md
│   ├── TROUBLESHOOTING.md
│   └── DEVELOPMENT.md
├── data/                   # Data directories
│   ├── rag_data/          # Knowledge base data
│   │   ├── documents.json
│   │   └── chroma_db/
│   └── rag_database/      # Vector database
│       └── chroma.sqlite3
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

## Architecture Overview

### Core Components

#### 1. Orchestrator (`src/macbot/orchestrator.py`)
- **Purpose**: Manages all services and their lifecycle
- **Features**:
  - Automatic startup and shutdown
  - Health monitoring
  - Service dependency management
  - Graceful error handling

#### 2. Voice Assistant (`src/macbot/voice_assistant.py`)
- **Purpose**: Main voice interaction interface
- **Features**:
  - Voice activity detection
  - Speech-to-text (Whisper)
  - LLM processing (llama.cpp)
  - Text-to-speech (Kokoro)
  - Tool integration

#### 3. Web Dashboard (`src/macbot/web_dashboard.py`)
- **Purpose**: Web-based monitoring and control interface
- **Features**:
  - Real-time system statistics
  - Service status monitoring
  - Chat interface
  - API endpoints

#### 4. RAG Server (`rag_server.py`)
- **Purpose**: Knowledge base and document search
- **Features**:
  - Document ingestion
  - Semantic search
  - Vector embeddings
  - ChromaDB integration

### Data Flow

```
Audio Input → VAD → Whisper (STT) → LLM → TTS → Audio Output
                      ↓
                Tool Calls → Native macOS Integration
                      ↓
                RAG Search → Knowledge Base
```

## Development Workflow

### 1. Create Feature Branch
```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes
Follow the coding standards and add tests for new functionality.

### 3. Test Changes
```bash
# Run unit tests
python -m pytest tests/

# Test integration
make test

# Manual testing
python -m macbot.voice_assistant --debug
```

### 4. Update Documentation
- Update relevant documentation files
- Add docstrings to new functions
- Update API documentation if endpoints change

### 5. Commit and Push
```bash
git add .
git commit -m "feat: add your feature description"
git push origin feature/your-feature-name
```

## Coding Standards

### Python Style
- Follow PEP 8
- Use type hints for function parameters and return values
- Add docstrings to all public functions and classes
- Use meaningful variable names

### Example Function
```python
def process_audio_data(audio_data: bytes, sample_rate: int = 16000) -> str:
    """
    Process audio data and return transcription.

    Args:
        audio_data: Raw audio bytes
        sample_rate: Audio sample rate in Hz

    Returns:
        Transcribed text from audio

    Raises:
        AudioProcessingError: If audio processing fails
    """
    # Implementation here
    pass
```

### Error Handling
- Use custom exceptions for specific error types
- Provide meaningful error messages
- Log errors with appropriate levels
- Don't expose internal errors to users

### Logging
```python
import logging

logger = logging.getLogger(__name__)

def some_function():
    logger.debug("Detailed debug information")
    logger.info("General information")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical error")
```

## Testing

### Unit Tests
```python
# tests/test_voice_assistant.py
import pytest
from macbot.voice_assistant import VoiceAssistant

class TestVoiceAssistant:
    def test_initialization(self):
        va = VoiceAssistant()
        assert va is not None

    def test_process_command(self):
        va = VoiceAssistant()
        result = va.process_command("test command")
        assert isinstance(result, str)
```

### Integration Tests
```python
# tests/test_integration.py
def test_full_pipeline():
    # Test complete audio -> response pipeline
    pass
```

### Running Tests
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_voice_assistant.py

# Run with coverage
pytest --cov=src --cov-report=html
```

## Adding New Tools

### 1. Define Tool Function
```python
# tools/custom_tools.py
def take_screenshot(save_path: str = "~/Desktop") -> str:
    """Take a screenshot and save to specified path."""
    import subprocess
    import os

    path = os.path.expanduser(save_path)
    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    filepath = os.path.join(path, filename)

    subprocess.run(["screencapture", "-x", filepath])
    return f"Screenshot saved to {filepath}"
```

### 2. Register Tool
```python
# src/macbot/voice_assistant.py
from .tools.custom_tools import take_screenshot

class VoiceAssistant:
    def __init__(self):
        self.tools = {
            "take_screenshot": take_screenshot,
            # ... other tools
        }
```

### 3. Add to Configuration
```yaml
# config.yaml
tools:
  enabled:
    - take_screenshot

  take_screenshot:
    save_path: "~/Desktop"
```

### 4. Update Prompts
```yaml
# config.yaml
prompts:
  system: |
    You can use these tools:
    - take_screenshot: Take a screenshot
    # ... other tools
```

## Performance Optimization

### Profiling
```python
import cProfile
import pstats

def profile_function():
    profiler = cProfile.Profile()
    profiler.enable()

    # Code to profile
    your_function()

    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative').print_stats(10)
```

### Memory Optimization
- Use generators for large data processing
- Implement proper cleanup in `__del__` methods
- Monitor memory usage with `tracemalloc`

### CPU Optimization
- Use multiprocessing for CPU-intensive tasks
- Implement caching for expensive operations
- Profile with `line_profiler`

## Debugging

### Debug Mode
```bash
# Enable debug logging
export DEBUG=1
python -m macbot.voice_assistant

# Or set in config
logging:
  level: DEBUG
```

### Remote Debugging
```python
# Add to code for remote debugging
import pdb; pdb.set_trace()
```

### Logging Configuration
```python
# config.yaml
logging:
  level: INFO
  format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
  file: macbot.log
```

## Contributing

### Pull Request Process
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Update documentation
6. Submit pull request

### Commit Message Format
```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style changes
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Maintenance

### Code Review Checklist
- [ ] Code follows style guidelines
- [ ] Tests are included
- [ ] Documentation is updated
- [ ] No breaking changes
- [ ] Performance impact assessed

## Deployment

### Local Deployment
```bash
# Full deployment
./scripts/start_macbot.sh

# Individual services
make run-assistant
make run-llama
python -m macbot.web_dashboard
```

### Production Considerations
- Set up log rotation
- Configure monitoring
- Set appropriate resource limits
- Implement backup strategies

## Security Considerations

### Local Security
- All services run locally - no external data exposure
- Microphone permissions required
- File system access limited to user directories

### Best Practices
- Keep dependencies updated
- Use virtual environments
- Don't commit sensitive configuration
- Regular security audits

## Support

### Getting Help
- Check existing issues on GitHub
- Review documentation
- Join community discussions

### Reporting Bugs
Include:
- System information
- Steps to reproduce
- Expected vs actual behavior
- Log excerpts
- Configuration used

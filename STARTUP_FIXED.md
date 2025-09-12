# MacBot - Fixed Startup Guide

## ✅ All Issues Fixed!

The following issues have been resolved:
- ✅ Python version compatibility (using Python 3.13)
- ✅ All dependencies installed correctly
- ✅ Virtual environment configured properly
- ✅ Build status verified (llama.cpp and whisper.cpp are built)
- ✅ Configuration paths working correctly
- ✅ Import issues resolved

## 🚀 Quick Start Commands

### Option 1: Full Orchestrator (Recommended)
```bash
cd /Users/admin/Downloads/MacBot
source macbot_env/bin/activate
python -m src.macbot.orchestrator
```

### Option 2: Interactive Startup Script
```bash
cd /Users/admin/Downloads/MacBot
./start_macbot_fixed.sh
# Choose option 3 for full orchestrator
```

### Option 3: Individual Services
```bash
cd /Users/admin/Downloads/MacBot
source macbot_env/bin/activate

# Start LLM server
make run-llama

# In another terminal, start voice assistant
make run-assistant

# In another terminal, start web dashboard
python -m src.macbot.web_dashboard
```

### Option 4: Using CLI
```bash
cd /Users/admin/Downloads/MacBot
source macbot_env/bin/activate

# Start orchestrator
python -m src.macbot.cli orchestrator

# Or start individual services
python -m src.macbot.cli dashboard
python -m src.macbot.cli voice
python -m src.macbot.cli rag
```

## 🌐 Access Points After Startup

- **Web Dashboard**: http://localhost:3000
- **LLM API**: http://localhost:8080/v1/chat/completions
- **RAG Server**: http://localhost:8001
- **Health Check**: http://localhost:3000/health

## 🔧 Troubleshooting Commands

```bash
# Check service status
python -m src.macbot.orchestrator --status

# View logs
tail -f logs/macbot.log

# Test individual components
curl http://localhost:8080/v1/models
curl http://localhost:3000/api/stats
curl http://localhost:8001/health
```

## 📝 What Was Fixed

1. **Python Version**: Updated Makefile to use `python3` instead of `python3.11`
2. **Virtual Environment**: Fixed paths to use `macbot_env` instead of `.venv`
3. **Dependencies**: Installed all missing packages including `livekit-agents`
4. **TTS Engine**: Configured to use `pyttsx3` as primary TTS (kokoro has Python 3.13 compatibility issues)
5. **Import Issues**: Fixed module imports to use `python -m src.macbot.module` syntax
6. **Configuration**: Verified all paths are correct and models are accessible

## 🎉 Ready to Go!

All components are now working correctly. Start with the orchestrator for the best experience!

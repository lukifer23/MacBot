# 🚀 MacBot Enhanced Features Guide

## Overview
MacBot has been significantly enhanced with advanced tool calling, comprehensive monitoring, and a beautiful web dashboard. All features run entirely offline using native macOS capabilities.

## 🛠️ Enhanced Tool Calling System

### **Native macOS Tools (No API Keys Required!)**

1. **🔍 Web Search**
   - **Command**: "search for [query]"
   - **Action**: Opens Safari with Google search results
   - **Example**: "search for weather forecast"

2. **🌐 Website Browsing**
   - **Command**: "browse [url]" or "open website [url]"
   - **Action**: Opens any URL in Safari
   - **Example**: "browse example.com"

3. **📱 App Launcher**
   - **Command**: "open app [appname]"
   - **Supported Apps**: Safari, Chrome, Finder, Terminal, Mail, Messages, FaceTime, Photos, Music, Calendar, Notes, Calculator
   - **Example**: "open app calculator"

4. **📸 Screenshot Tool**
   - **Command**: "take screenshot" or "take picture"
   - **Action**: Captures entire screen using macOS `screencapture`
   - **Output**: Saves to Desktop with timestamp

5. **🌤️ Weather App**
   - **Command**: "weather"
   - **Action**: Opens macOS Weather app
   - **Example**: "what's the weather like"

6. **💻 System Monitoring**
   - **Command**: "system info"
   - **Output**: Real-time CPU, RAM, and disk usage

7. **🧠 RAG Knowledge Base**
   - **Command**: "search knowledge base for [query]"
   - **Features**: Document ingestion, semantic search, vector database

## 🌐 Web Dashboard Features

### **Real-Time Monitoring**
- **Live Stats**: CPU, RAM, Disk, Network usage
- **Auto-refresh**: Updates every 5 seconds
- **Service Status**: Visual indicators for all components
- **Endpoint URLs**: Direct links to all services

### **Service Cards**
- **LLM Server**: llama.cpp status and endpoint
- **Voice Assistant**: Process status and interface info
- **RAG System**: Knowledge base availability
- **Web Dashboard**: Self-monitoring status

### **Chat Interface**
- **Text Input**: Type messages directly
- **Real-time Responses**: Instant feedback
- **Message History**: Scrollable conversation log
- **API Integration**: Ready for LLM integration

### **API Endpoints**
```
GET  /api/stats      - System statistics
GET  /api/services   - Service health status
POST /api/chat       - Chat interface
```

## 🎯 Central Orchestrator

### **Service Management**
- **Automatic Startup**: All services in correct order
- **Health Monitoring**: Continuous service checking
- **Graceful Shutdown**: Clean process termination
- **Error Recovery**: Automatic restarts on failure

### **Monitoring Features**
- **Process Health**: Checks every 10 seconds
- **Resource Usage**: CPU, RAM, disk monitoring
- **Service URLs**: All endpoints displayed
- **Status Reporting**: Real-time service status

## 🚀 Getting Started

### **Quick Start**
```bash
# Start everything with one command
./start_macbot.sh

# Choose option 3 for full orchestrator
# Choose option 6 to open web dashboard
```

### **Manual Control**
```bash
# Start orchestrator
python orchestrator.py

# Check status
python orchestrator.py --status

# Stop everything
python orchestrator.py --stop
```

### **Individual Services**
```bash
# Start llama server
make run-llama

# Start voice assistant
make run-assistant

# Start web dashboard
python web_dashboard.py
```

## 📊 System Requirements

### **Hardware**
- **CPU**: 2-3 cores recommended (preserves resources for other work)
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 5GB for models and dependencies
- **GPU**: Apple Silicon (M1/M2/M3) with Metal support

### **Software**
- **macOS**: 12.0+ (Monterey)
- **Python**: 3.11+
- **Homebrew**: For system dependencies

## 🔧 Configuration

### **Key Files**
- `config.yaml` - Main configuration
- `Makefile` - Build and run commands
- `orchestrator.py` - Service management
- `web_dashboard.py` - Web interface
- `voice_assistant.py` - Enhanced voice assistant

### **Model Configuration**
- **LLM**: Qwen3-4B-Instruct-2507 (2.3GB)
- **STT**: Whisper.cpp base.en (141MB)
- **TTS**: Kokoro (82M)
- **Embeddings**: Sentence Transformers

## 🎉 Voice Commands Examples

### **System Control**
```
"system info"           → Get system status
"take screenshot"       → Capture screen
"open app terminal"     → Launch Terminal
```

### **Web & Search**
```
"search for news"       → Web search
"browse github.com"     → Open website
"weather"               → Check weather
```

### **Knowledge & Help**
```
"search knowledge base for documents"
"help"                  → Get assistance
"what can you do?"      → List capabilities
```

## 🚨 Troubleshooting

### **Common Issues**
1. **Web Dashboard Not Loading**
   - Check if orchestrator is running
   - Verify port 3000 is available
   - Check firewall settings

2. **Voice Assistant Not Responding**
   - Ensure llama server is running on port 8080
   - Check microphone permissions
   - Verify Whisper model is loaded

3. **Tools Not Working**
   - Check macOS permissions for apps
   - Verify Python dependencies are installed
   - Check system resource availability

### **Logs & Debugging**
```bash
# Check orchestrator logs
tail -f macbot.log

# Check service status
python orchestrator.py --status

# Test individual components
python web_dashboard.py
python voice_assistant.py
```

## 🔮 Future Enhancements

### **Planned Features**
- **Advanced RAG**: Document upload interface
- **Custom Tools**: User-defined tool creation
- **Model Management**: Easy model switching
- **Performance Metrics**: Detailed latency tracking
- **Mobile Interface**: Responsive design improvements

### **Integration Possibilities**
- **HomeKit**: Smart home control
- **Shortcuts**: macOS automation
- **Calendar**: Schedule management
- **Email**: Mail composition and reading

## 📞 Support

### **Documentation**
- **README.md** - Basic setup and usage
- **ENHANCED_FEATURES.md** - This comprehensive guide
- **Makefile** - Build and deployment commands

### **Getting Help**
1. Check the logs for error messages
2. Verify all dependencies are installed
3. Ensure system resources are available
4. Test individual components separately

---

**MacBot Enhanced** - Your local AI assistant with the power of native macOS tools! 🚀

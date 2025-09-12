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

### **Real-Time WebSocket Communication**
- **Bidirectional Communication**: Flask-SocketIO enables real-time, two-way communication between web clients and the MacBot backend
- **Live Updates**: All conversation events, system stats, and state changes are broadcast instantly to connected clients
- **Event-Driven Architecture**: WebSocket events handle chat messages, voice recording, conversation interruption, and system monitoring

### **Real-Time Monitoring**
- **Live Stats**: CPU, RAM, Disk, Network usage with 5-second auto-refresh
- **Service Status**: Visual indicators for all components with real-time health updates
- **Conversation Tracking**: Live conversation state, message count, and interruption monitoring
- **Endpoint URLs**: Direct links to all services with connection status

### **Interactive Controls**
- **Voice Recording**: Start/stop voice recording with real-time feedback
- **Conversation Management**: Interrupt active conversations, clear conversation history
- **Manual Intervention**: Direct control over conversation flow and system state
- **Real-Time Feedback**: Instant visual feedback for all user actions
- **Document Upload**: Upload documents to RAG knowledge base for enhanced search capabilities

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
GET  /api/stats           - System statistics
GET  /api/services        - Service health status
POST /api/chat            - Chat interface
POST /api/upload-documents - Upload documents to RAG system
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

## �️ Health Monitoring & Resilience

### **Circuit Breaker Pattern**
- **Automatic Failure Detection**: Monitors service health with configurable thresholds
- **Intelligent Recovery**: Automatically retries failed services after timeout periods
- **Service Isolation**: Prevents one failing service from affecting the entire system
- **Configurable Thresholds**: Customizable failure counts and recovery timeouts

### **Graceful Degradation**
- **Degraded Mode Responses**: Provides helpful responses even when LLM is unavailable
- **Basic Query Support**: Time, date, and help queries work without external services
- **Service Availability Checks**: Automatically detects and adapts to service failures
- **User-Friendly Messaging**: Clear communication about service status and limitations

### **Comprehensive Health Monitoring**
- **Multi-Service Tracking**: Monitors LLM server, RAG server, web dashboard, and system resources
- **Real-Time Status**: Live health status updates via WebSocket and REST API
- **Alert System**: Configurable alerts for service failures and recoveries
- **Health Endpoints**: REST API endpoints for programmatic health checking

### **Automatic Recovery Mechanisms**
- **Service Restart**: Automatic restart of failed processes
- **Dependency Management**: Proper startup order and dependency resolution
- **Resource Monitoring**: System resource tracking to prevent overload
- **Process Health Checks**: Continuous monitoring of all running processes

### **Web Dashboard Health Integration**
- **Health Status Display**: Visual indicators for all service health states
- **Real-Time Updates**: Live health status broadcasting via WebSocket
- **Health API Endpoint**: `/health` REST endpoint for comprehensive status
- **Service Cards**: Individual service health cards with detailed status

## 🔒 Code Quality & Security Improvements

### **PEP8 Compliance & Code Standards**
- **Import Organization**: Separated multi-line imports for better readability
- **Duplicate Code Removal**: Eliminated redundant code across modules
- **Type Hints**: Added comprehensive type annotations for better code documentation
- **Constants Extraction**: Moved hardcoded values to named constants for maintainability

### **Enhanced Error Handling**
- **Specific Exception Handling**: Replaced bare `except` clauses with specific exception types
- **Input Validation**: Added robust input validation for all user inputs
- **Graceful Error Recovery**: Improved error messages and recovery mechanisms
- **Logging Improvements**: Enhanced logging with structured error information

### **Security Enhancements**
- **Input Sanitization**: All user inputs are properly sanitized and validated
- **Service Availability Checks**: Added checks for LLM and RAG service availability
- **Resource Protection**: Implemented resource usage monitoring and limits
- **Secure Configuration**: Environment-based configuration with secure defaults

### **Performance Optimizations**
- **Import Optimization**: Removed duplicate and unnecessary imports
- **Memory Management**: Improved memory usage patterns
- **Process Efficiency**: Optimized service startup and communication
- **Resource Monitoring**: Added comprehensive system resource tracking

### **Code Maintainability**
- **Modular Architecture**: Clean separation of concerns across modules
- **Documentation Updates**: Comprehensive documentation for all new features
- **Testing Framework**: Enhanced test coverage for critical functions
- **Configuration Management**: Centralized configuration with validation

## �🚀 Getting Started

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
python -m macbot.orchestrator --status

# Stop everything
python -m macbot.orchestrator --stop
```

### **Individual Services**
```bash
# Start llama server
make run-llama

# Start voice assistant
make run-assistant

# Start web dashboard
python -m macbot.web_dashboard
```

## 📊 System Requirements

### **Hardware**
- **CPU**: 2-3 cores recommended (preserves resources for other work)
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 5GB for models and dependencies
- **GPU**: Apple Silicon (M1/M2/M3) with Metal support

### **Software**
- **macOS**: 12.0+ (Monterey or later)
- **Python**: 3.13+ (optimized for Apple Silicon)
- **Homebrew**: For system dependencies
- **TTS Engines**: Piper (primary) + Kokoro framework
- **STT Engine**: Whisper.cpp v1.7.6 with Metal acceleration

## 🔧 Configuration

### **Key Files**
- `config.yaml` - Main configuration
- `Makefile` - Build and run commands
- `src/macbot/orchestrator.py` - Service management
- `src/macbot/web_dashboard.py` - Web interface
- `src/macbot/voice_assistant.py` - Enhanced voice assistant

### **Model Configuration**
- **LLM**: Qwen3-4B-Instruct-2507-Q4_K_M (2.3GB)
- **STT**: Whisper Large v3 Turbo Q5_0 (547MB) - Metal accelerated
- **TTS**: Piper Neural (82MB) + Kokoro framework ready
- **Embeddings**: Sentence Transformers (via ChromaDB)
- **Performance**: ~0.2s STT latency, 178 WPM TTS speed

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

## 🎤 Interruptible Conversation System

### **Natural Conversation Flow**
MacBot now supports natural, interruptible conversations with barge-in capability:

- **Real-time Interruption**: Speak while MacBot is responding to interrupt immediately
- **Context Preservation**: Full conversation history maintained across interruptions
- **Seamless Experience**: Natural back-and-forth without waiting for responses to complete

### **How It Works**
1. **Voice Activity Detection**: System continuously monitors for user speech
2. **Instant Interruption**: TTS playback stops immediately when user speaks
3. **Context Management**: Previous conversation context is preserved
4. **Immediate Processing**: New user input is processed without delay

### **Configuration Options**
```yaml
interruption:
  enabled: true                    # Enable/disable interruption
  voice_threshold: 0.3            # Voice detection sensitivity (0.0-1.0)
  cooldown_period: 1.0            # Minimum time between interruptions (seconds)
  interruption_timeout: 5.0       # Max wait time for interruption (seconds)
  buffer_size: 100                # Conversation history buffer size
```

### **Technical Implementation**
- **Audio Interrupt Handler**: macOS AudioQueue-based TTS interruption
- **Conversation Manager**: State machine with context buffering
- **Thread-safe Operations**: Concurrent audio playback with interruption monitoring
- **Fallback Mechanisms**: Graceful degradation when interruption is disabled

### **Usage Examples**
- Start speaking naturally during MacBot's responses
- Interrupt with questions like "Wait, actually..." or "No, I meant..."
- Continue conversations seamlessly without losing context
- Natural conversation flow similar to human-to-human interaction

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
tail -f logs/macbot.log

# Check service status
python -m macbot.orchestrator --status

# Test individual components
python -m macbot.web_dashboard
python -m macbot.voice_assistant
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

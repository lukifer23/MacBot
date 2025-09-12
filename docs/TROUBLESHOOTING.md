# MacBot Troubleshooting Guide

## Recent Updates (Phase 6 - Critical Fixes)

**Last Updated:** Post Phase 6 Completion
**Status:** All critical architecture issues resolved

### ✅ **Resolved Issues**
- Message bus WebSocket/queue communication mismatch
- TTS interruption race conditions and complex fallback logic
- Conversation state synchronization problems
- Circuit breaker datetime comparison errors
- Code duplicates and quality issues
- Missing message bus integration in voice assistant

### 🔧 **System Health**
All modules now compile successfully and critical communication paths are verified.

## View Logs

## Overview
This guide helps you diagnose and resolve common issues with MacBot. Start with the quick diagnostics, then follow the specific issue sections.

## Quick Diagnostics

### 1. Check Service Status
```bash
# Check all services
python orchestrator.py --status

# Check specific service
python orchestrator.py --status --service llama_server
```

### 2. View Logs
```bash
# View main log
tail -f macbot.log

# Check service-specific logs
tail -f llama.cpp/server.log
tail -f whisper.cpp/whisper.log
```

### 3. Test Individual Components
```bash
# Test LLM server
curl http://localhost:8080/health

# Test web dashboard
curl http://localhost:3000/api/stats

# Test RAG server
curl http://localhost:8081/health
```

### 4. Health Monitoring & Resilience
```bash
# Check health status via API
curl http://localhost:3000/health

# Test circuit breaker status
python -c "from src.macbot.health_monitor import get_health_monitor; hm = get_health_monitor(); print(hm.get_health_status())"

# Check degraded mode
python -c "from src.macbot.voice_assistant import get_degraded_response; print(get_degraded_response('What time is it?'))"
```

## Health Monitoring & Resilience Issues

### Circuit Breaker Tripped

#### Symptom
Services show as unhealthy and circuit breaker is open.

#### Solutions
1. **Check service logs:**
   ```bash
   # View health monitor logs
   tail -f logs/macbot.log | grep -i health
   
   # Check specific service logs
   tail -f models/llama.cpp/server.log
   ```

2. **Manual service restart:**
   ```bash
   # Restart specific service
   python orchestrator.py --restart llama_server
   
   # Reset circuit breaker
   python -c "from src.macbot.health_monitor import get_health_monitor; hm = get_health_monitor(); hm.reset_circuit_breaker('llama_server')"
   ```

3. **Check network connectivity:**
   ```bash
   # Test service endpoints
   curl http://localhost:8080/v1/models
   curl http://localhost:8081/health
   ```

### Degraded Mode Not Working

#### Symptom
Services fail but system doesn't provide degraded responses.

#### Solutions
1. **Check health monitor configuration:**
   ```yaml
   health_monitor:
     check_interval: 30
     timeout: 10
     failure_threshold: 3
   ```

2. **Verify degraded response function:**
   ```bash
   python -c "from src.macbot.voice_assistant import get_degraded_response; print('Degraded response test:', get_degraded_response('hello'))"
   ```

3. **Check service availability detection:**
   ```bash
   # Test service health checks
   curl http://localhost:8080/health
   curl http://localhost:8081/health
   ```

### Health Endpoint Not Responding

#### Symptom
`/health` endpoint returns errors or doesn't respond.

#### Solutions
1. **Check web dashboard status:**
   ```bash
   # Verify web dashboard is running
   ps aux | grep web_dashboard
   
   # Check web dashboard logs
   tail -f logs/web_dashboard.log
   ```

2. **Test health monitor import:**
   ```bash
   python -c "from src.macbot.health_monitor import get_health_monitor; print('Health monitor import successful')"
   ```

3. **Restart web dashboard:**
   ```bash
   # Kill existing process
   pkill -f web_dashboard
   
   # Restart
   python -m macbot.web_dashboard
   ```

### Automatic Recovery Not Working

#### Symptom
Services fail but don't automatically restart.

#### Solutions
1. **Check orchestrator configuration:**
   ```yaml
   orchestrator:
     auto_restart: true
     restart_delay: 5
   ```

2. **Verify process monitoring:**
   ```bash
   # Check running processes
   python orchestrator.py --status
   
   # View orchestrator logs
   tail -f logs/macbot.log | grep -i restart
   ```

3. **Manual process restart:**
   ```bash
   python orchestrator.py --restart all
   ```

## Common Issues

### Voice Assistant Won't Start

#### Symptom
Voice assistant fails to start with microphone errors.

#### Solutions
1. **Check microphone permissions:**
   ```bash
   # Grant microphone access to Terminal
   # System Settings → Privacy & Security → Microphone
   ```

2. **Verify audio devices:**
   ```bash
   # List available audio devices
   python -c "import pyaudio; p = pyaudio.PyAudio(); [print(f'{i}: {p.get_device_info_by_index(i)[\"name\"]}') for i in range(p.get_device_count())]; p.terminate()"
   ```

3. **Update device indices in config:**
   ```yaml
   voice_assistant:
     microphone_device: 1  # Try different indices
     speaker_device: 1
   ```

#### Symptom
Voice assistant starts but doesn't respond to voice.

#### Solutions
1. **Check VAD threshold:**
   ```yaml
   voice_assistant:
     vad_threshold: 0.3  # Lower = more sensitive
   ```

2. **Test microphone input:**
   ```bash
   # Record a test audio file
   rec test.wav trim 0 3

   # Test with Whisper
   ./models/whisper.cpp/build/bin/whisper-cli -m models/whisper.cpp/models/ggml-base.en.bin -f test.wav
   ```

3. **Check for background noise** - try in a quieter environment.

### LLM Server Issues

#### Symptom
LLM server fails to start.

#### Solutions
1. **Check model file:**
   ```bash
   # Verify model exists and is readable
   ls -lh models/llama.cpp/models/*.gguf

   # Check file permissions
   chmod 644 models/llama.cpp/models/*.gguf
   ```

2. **Insufficient memory:**
   ```bash
   # Check available RAM
   vm_stat | grep "Pages free"

   # Reduce context length in config
   models:
     llm:
       context_length: 2048
   ```

3. **Port already in use:**
   ```bash
   # Find process using port 8080
   lsof -i :8080

   # Kill conflicting process
   kill -9 <PID>
   ```

#### Symptom
LLM responses are slow or stuttering.

#### Solutions
1. **Reduce context length:**
   ```yaml
   models:
     llm:
       context_length: 2048
   ```

2. **Limit CPU threads:**
   ```yaml
   models:
     llm:
       threads: 4
   ```

3. **Use smaller model** - try a 3B or 7B parameter model instead of larger ones.

### Web Dashboard Issues

#### Symptom
Dashboard not accessible.

#### Solutions
1. **Check if service is running:**
   ```bash
   python orchestrator.py --status
   ```

2. **Verify port availability:**
   ```bash
   # Check if port 3000 is in use
   lsof -i :3000
   ```

3. **Firewall settings** - ensure local connections are allowed.

#### Symptom
Dashboard loads but shows no data.

#### Solutions
1. **Check API endpoints:**
   ```bash
   curl http://localhost:3000/api/stats
   curl http://localhost:3000/api/services
   ```

2. **Restart services:**
   ```bash
   python orchestrator.py --restart
   ```

### RAG System Issues

#### Symptom
Knowledge base search not working.

#### Solutions
1. **Check RAG server status:**
   ```bash
   curl http://localhost:8081/health
   ```

2. **Verify database files:**
   ```bash
   ls -lh data/rag_database/
   ```

3. **Rebuild knowledge base:**
   ```bash
   # Remove old database
   rm -rf data/rag_database/

   # Restart RAG server
   python -m macbot.orchestrator --restart --service rag_server
   ```

### Build Issues

#### Symptom
Whisper or llama.cpp build fails.

#### Solutions
1. **Install dependencies:**
   ```bash
   brew install cmake ffmpeg portaudio python@3.11
   ```

2. **Clean and rebuild:**
   ```bash
   # For llama.cpp
   cd models/llama.cpp
   rm -rf build/
   make clean
   cd ../..

   # For whisper.cpp
   cd models/whisper.cpp
   rm -rf build/
   make clean
   cd ../..
   ```

3. **Check Xcode command line tools:**
   ```bash
   xcode-select --install
   ```

### Performance Issues

#### Symptom
System is slow or unresponsive.

#### Solutions
1. **Monitor resource usage:**
   ```bash
   # Check CPU and memory
   top -pid $(pgrep -f "python orchestrator.py")

   # Check disk space
   df -h
   ```

2. **Reduce model sizes:**
   - Use smaller Whisper model (`tiny.en` or `base.en`)
   - Use smaller LLM (3B-7B parameters)

3. **Limit concurrent processes:**
   ```yaml
   services:
     orchestrator:
       max_processes: 3
   ```

### Audio Quality Issues

#### Symptom
Poor audio quality or distortion.

#### Solutions
1. **Check audio settings:**
   ```yaml
   voice_assistant:
     sample_rate: 16000
     channels: 1
   ```

2. **Test audio devices:**
   ```bash
   # Play test sound
   say "Test audio output"

   # Record and playback
   rec test.wav trim 0 2 && play test.wav
   ```

3. **Update audio drivers** - ensure macOS is up to date.

## Advanced Troubleshooting

### Debug Mode
Enable debug logging for detailed information:

```bash
export DEBUG=1
python -m macbot.voice_assistant
```

### Manual Service Testing
Test each component individually:

```bash
# Test LLM only
cd models/llama.cpp
./build/bin/llama-server --model models/your-model.gguf --port 8080

# Test Whisper only
cd models/whisper.cpp
./build/bin/whisper-cli --model models/ggml-base.en.bin --file your-audio.wav

# Test TTS only
python -c "import kokoro; print('TTS working')"
```

### Log Analysis
Search for specific errors:

```bash
# Search for errors in logs
grep -i "error\|failed\|exception" macbot.log

# Check for memory issues
grep -i "memory\|out of memory" macbot.log
```

### System Resource Monitoring
Monitor system resources during operation:

```bash
# Real-time monitoring
while true; do
  echo "$(date): CPU $(top -l 1 | grep "CPU usage")"
  sleep 5
done
```

## Code Quality & Security Issues

### Import Errors After Updates

#### Symptom
Modules fail to import after Phase 5 updates with import-related errors.

#### Solutions
1. **Check Python path:**
   ```bash
   # Verify Python can find modules
   python -c "import sys; print(sys.path)"
   
   # Add project root to path if needed
   export PYTHONPATH="/Users/admin/Downloads/MacBot:$PYTHONPATH"
   ```

2. **Reinstall dependencies:**
   ```bash
   # Recreate virtual environment
   rm -rf macbot_env/
   python -m venv macbot_env
   source macbot_env/bin/activate
   pip install -r requirements.txt
   ```

3. **Check for circular imports:**
   ```bash
   # Test individual imports
   python -c "from src.macbot.voice_assistant import VoiceAssistant"
   python -c "from src.macbot.orchestrator import Orchestrator"
   ```

### Type Hint Errors

#### Symptom
Type checking fails with type annotation errors.

#### Solutions
1. **Install type checking tools:**
   ```bash
   pip install mypy black isort
   ```

2. **Run type checker:**
   ```bash
   # Check types in source directory
   mypy src/macbot/
   ```

3. **Fix type annotations:**
   ```python
   # Common fixes
   from typing import Optional, List, Dict
   
   def process_input(self, text: str) -> Optional[str]:
       # Function implementation
   ```

### Input Validation Failures

#### Symptom
System rejects valid inputs or accepts invalid ones.

#### Solutions
1. **Check validation rules:**
   ```python
   # Test input validation
   from src.macbot.voice_assistant import validate_input
   
   print(validate_input("Hello world"))  # Should return True
   print(validate_input(""))  # Should return False
   ```

2. **Review validation constants:**
   ```yaml
   voice_assistant:
     max_input_length: 1000
     min_input_length: 1
   ```

3. **Test edge cases:**
   ```bash
   # Test with various inputs
   python -c "from src.macbot.voice_assistant import validate_input; print('Test:', validate_input('A' * 2000))"
   ```

### Security Validation Errors

#### Symptom
Security checks fail or block legitimate operations.

#### Solutions
1. **Check service availability:**
   ```bash
   # Test LLM service
   python -c "from src.macbot.voice_assistant import check_llm_service_available; print(check_llm_service_available())"
   ```

2. **Verify configuration:**
   ```yaml
   security:
     enable_input_validation: true
     max_request_size: 1048576  # 1MB
   ```

3. **Test sanitization:**
   ```python
   # Test input sanitization
   from src.macbot.voice_assistant import sanitize_input
   
   clean_input = sanitize_input("<script>alert('test')</script>Hello")
   print(clean_input)  # Should be "Hello"
   ```

### Performance Degradation

#### Symptom
System performance worsens after code quality updates.

#### Solutions
1. **Profile code execution:**
   ```bash
   # Install profiler
   pip install cProfile
   
   # Profile main functions
   python -m cProfile -s time src/macbot/voice_assistant.py
   ```

2. **Check for memory leaks:**
   ```bash
   # Monitor memory usage
   python -c "import psutil; print(f'Memory: {psutil.virtual_memory().percent}%')"
   ```

3. **Optimize imports:**
   ```python
   # Use lazy imports for heavy modules
   try:
       import heavy_module
   except ImportError:
       heavy_module = None
   ```

### Configuration Validation Errors

#### Symptom
Configuration fails to load with validation errors.

#### Solutions
1. **Validate configuration file:**
   ```bash
   # Check YAML syntax
   python -c "import yaml; yaml.safe_load(open('config/config.yaml'))"
   ```

2. **Check required fields:**
   ```yaml
   # Ensure all required sections exist
   voice_assistant:
     enabled: true
   models:
     llm:
       model_path: "models/llama.cpp/models/..."
   ```

3. **Environment variables:**
   ```bash
   # Check environment setup
   echo $MACBOT_CONFIG
   echo $MACBOT_ENV
   ```

## Getting Help

If you can't resolve an issue:

1. **Check the GitHub repository** for known issues
2. **Collect diagnostic information:**
   ```bash
   # System info
   system_profiler SPSoftwareDataType SPHardwareDataType

   # Python environment
   python --version
   pip list

   # Service versions
   ./models/llama.cpp/build/bin/llama-server --version
   ```
3. **Include relevant log excerpts** when reporting issues

## Prevention

### Regular Maintenance
- Keep models and software updated
- Monitor disk space and clean logs periodically
- Restart services weekly to clear memory leaks

### Backup Important Data
```bash
# Backup configuration
cp config.yaml config.yaml.backup

# Backup knowledge base
cp -r data/rag_database/ data/rag_database_backup/
```

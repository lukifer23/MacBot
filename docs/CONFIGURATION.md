# MacBot Configuration Guide

## Overview
MacBot uses a YAML configuration file (`config.yaml`) to manage all settings. This file controls model paths, voice settings, system prompts, and service configurations.

## Configuration File Structure

```yaml
# Model Configuration
models:
  llm:
    path: "models/llama.cpp/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    context_length: 8192
    threads: -1  # Use all available cores
    temperature: 0.4
    max_tokens: 200

  stt:
    model: "models/whisper.cpp/models/ggml-large-v3-turbo-q5_0.bin"
    language: "en"

  tts:
    voice: "en_US-lessac-medium"  # Piper neural voice
    speed: 1.0      # Speech speed multiplier

# Voice Assistant Settings
voice_assistant:
  microphone_device: 0
  speaker_device: 0
  vad_threshold: 0.5
  silence_timeout: 1.0
  wake_word: "hey macbot"

# System Prompts
prompts:
  system: |
    You are MacBot, a helpful AI assistant running locally on macOS.
    You have access to various tools and can help with tasks on this computer.

  tool_use: |
    When using tools, be concise and provide clear instructions.

# Service Configuration
services:
  web_dashboard:
    port: 3000
    host: "0.0.0.0"

  rag_server:
    port: 8081
    host: "localhost"
    collection_name: "macbot_docs"

  orchestrator:
    check_interval: 10  # seconds
    auto_restart: true

# Health Monitoring Configuration
health_monitor:
  enabled: true
  check_interval: 30  # seconds between health checks
  timeout: 10  # seconds to wait for service response
  failure_threshold: 3  # consecutive failures before marking unhealthy
  recovery_timeout: 60  # seconds to wait before retrying failed service
  
  services:
    llm_server:
      url: "http://localhost:8080/health"
      timeout: 5
      enabled: true
      
    rag_server:
      url: "http://localhost:8081/health"
      timeout: 5
      enabled: true
      
    web_dashboard:
      url: "http://localhost:3000/health"
      timeout: 2
      enabled: true
      
    system_resources:
      cpu_threshold: 90  # percent
      memory_threshold: 90  # percent
      disk_threshold: 95  # percent
      enabled: true

  circuit_breakers:
    llm_server:
      failure_threshold: 5
      recovery_timeout: 30
      enabled: true
      
    rag_server:
      failure_threshold: 5
      recovery_timeout: 30
      enabled: true

  alerts:
    enabled: true
    log_level: "WARNING"
    email_enabled: false
    email_recipient: "admin@example.com"

# Tool Configuration
tools:
  enabled:
    - web_search
    - screenshot
    - app_launcher
    - system_monitor
    - weather
    - rag_search

  web_search:
    default_engine: "google"
    timeout: 10

  screenshot:
    save_path: "~/Desktop"
    format: "png"

  app_launcher:
    allowed_apps:
      - Safari
      - Terminal
      - Finder
      - Mail
      - Messages
```

## Model Configuration

### LLM Models
- **Path**: Absolute or relative path to your GGUF model file
- **Context Length**: Maximum context window (reduce for lower memory usage)
- **Threads**: Number of CPU threads to use (-1 for all available)

### STT Models
- **Model**: Whisper model path or size
  - `tiny` (39 MB) - Fastest, least accurate
  - `base` (74 MB) - Good balance
  - `small` (244 MB) - Better accuracy
  - `medium` (1.5 GB) - High accuracy
  - `large-v3-turbo` (1.5 GB) - Best accuracy, Metal accelerated
  - **Recommended**: `large-v3-turbo-q5_0` (547 MB quantized)
- **Language**: Language code for transcription (`en`, `es`, `fr`, etc.)
- **Performance**: ~0.2s latency with Metal acceleration

### TTS Models
- **Engine Priority**: Kokoro (interruptible) → Piper (neural) → pyttsx3 (fallback)
- **Piper Voices**: `en_US-lessac-medium`, `en_GB-alan-medium`, etc.
- **Kokoro Voices**: Framework ready for advanced interruptible TTS
- **pyttsx3 Voices**: System voices (185 available on macOS)
- **Speed**: Speech speed multiplier (0.5-2.0)
- **Performance**: 178 WPM with natural prosody

## Voice Assistant Settings

### Audio Configuration
- **Microphone Device**: Audio input device index (0 for default)
- **Speaker Device**: Audio output device index (0 for default)
- **VAD Threshold**: Voice activity detection sensitivity (0.1-0.9)
- **Silence Timeout**: Seconds of silence before processing
- **Wake Word**: Optional wake word for activation

## System Prompts

### Customizing Prompts
You can modify the system prompts to change MacBot's behavior:

```yaml
prompts:
  system: |
    You are MacBot, a specialized assistant for [your use case].
    [Additional instructions here]

  tool_use: |
    When using tools, always [specific instructions].
```

## Service Configuration

### Web Dashboard
- **Port**: Port number for the web interface
- **Host**: Bind address (0.0.0.0 for all interfaces)

### RAG Server
- **Port**: Port for the RAG service
- **Host**: Bind address
- **Collection Name**: Name for the vector database collection
- **API Tokens**: List of allowed tokens for `/api/*` routes
- **Rate Limit Per Minute**: Requests allowed per token each minute

#### API Authentication
Configure tokens to secure the RAG API:

```yaml
services:
  rag_server:
    api_tokens:
      - "my-secret-token"
    rate_limit_per_minute: 60
```

Clients must include the token in an `Authorization` header:

```
Authorization: Bearer my-secret-token
```

Requests missing or using invalid tokens receive `401 Unauthorized`. Exceeding
the rate limit returns `429 Too Many Requests`.

### Orchestrator
- **Check Interval**: How often to check service health (seconds)
- **Auto Restart**: Automatically restart failed services

## Tool Configuration

### Enabling/Disabling Tools
Add or remove tools from the `enabled` list:

```yaml
tools:
  enabled:
    - web_search
    - screenshot
    # - app_launcher  # commented out to disable
```

### Tool-Specific Settings

#### Web Search
```yaml
web_search:
  default_engine: "google"  # or "duckduckgo", "bing"
  timeout: 10  # seconds
```

#### Screenshot
```yaml
screenshot:
  save_path: "~/Desktop"
  format: "png"  # or "jpg"
```

#### App Launcher
```yaml
app_launcher:
  allowed_apps:
    - Safari
    - Terminal
    - Finder
    # Add more apps as needed
```

## Environment Variables

You can override configuration values using environment variables:

```bash
export LLAMA_MODEL_PATH="/path/to/model.gguf"
export WHISPER_MODEL="small.en"
export MACBOT_PORT=3001
```

## Configuration Validation

The system validates your configuration on startup. Common issues:

1. **Invalid model paths**: Ensure model files exist and are readable
2. **Port conflicts**: Check if ports are already in use
3. **Device indices**: Verify audio device indices are valid

## Advanced Configuration

### Custom Tools
You can add custom tools by extending the configuration:

```yaml
tools:
  custom_tools:
    my_tool:
      command: "python /path/to/script.py"
      description: "My custom tool"
```

### Performance Tuning
For better performance on lower-end hardware:

```yaml
models:
  llm:
    context_length: 2048  # Reduce context
    threads: 4  # Limit CPU threads

voice_assistant:
  vad_threshold: 0.7  # Less sensitive VAD
```

## Configuration Reload

Changes to `config.yaml` require restarting services to take effect. Use the orchestrator to stop then start services:

```bash
python -m macbot.orchestrator --stop
python -m macbot.orchestrator
```

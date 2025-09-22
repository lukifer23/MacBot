# MacBot Configuration Guide (Current)

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
    # Piper-only TTS
    piper:
      voice_path: "piper_voices/en_US-lessac-medium/model.onnx" # absolute or relative path
      sample_rate: 22050
      reload_sec: 30  # heartbeat to re-init Piper if needed
    speed: 1.0      # Speech speed multiplier

# Voice Assistant Settings
voice_assistant:
  # Conversation + interruption settings
  interruption:
    enabled: true
    interrupt_threshold: 0.01
    interrupt_cooldown: 0.5
    conversation_timeout: 30
    context_buffer_size: 10

  # Audio routing and anti-feedback during TTS
  audio:
    mic_mute_while_tts: true
    output_device: null   # CoreAudio device index or name (optional)
    input_device: null    # CoreAudio device index or name (optional)

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

  voice_assistant:
    host: "localhost"
    port: 8123
  rag_server:
    port: 8001
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

# Authentication Configuration
auth:
  enabled: true
  jwt_secret: null  # Set via MACBOT_JWT_SECRET environment variable
  token_expiry_hours: 24
  algorithm: "HS256"
  issuer: "macbot"
  audience: "macbot-api"

# Input Validation Configuration
validation:
  enabled: true
  max_text_length: 10000
  max_audio_size: 10485760  # 10MB
  xss_protection: true
  sql_injection_protection: true
  command_injection_protection: true

# Resource Management Configuration
resource_management:
  enabled: true
  temp_file_cleanup: true
  thread_pool_timeout: 30
  memory_monitoring: true
  cleanup_interval: 300
  max_temp_files: 100
  max_thread_pools: 10

# Error Handling Configuration
error_handling:
  enabled: true
  log_level: "INFO"
  structured_logging: true
  correlation_ids: true
  max_error_context: 1000

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
- **Max Tokens**: Maximum number of completion tokens per response (dashboard and voice assistant both honor `models.llm.max_tokens`). Increase for longer replies.

### STT Models
- Whisper.cpp is used via CLI for browser audio; python bindings (if present) are used as a fallback.
- Set the model path under `models.stt.model` in `config.yaml` (e.g., `models/whisper.cpp/models/ggml-base.en.bin`).

### TTS (Piper)
- Piper is the sole TTS engine. Place voices in `piper_voices/*/model.onnx` for auto-discovery in the dashboard.
- The dashboard exposes a Voice Settings section to preview/apply voices.
- Audio output device can be changed at runtime via VA control API or persisted in config.

### CORS & Control Server
- The Voice Assistant control server enables CORS for `http://127.0.0.1:3000` and `http://localhost:3000` so the dashboard can call `/voices`, `/set-voice`, etc.

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
- **API Tokens**: List of allowed tokens for `/api/*` routes (set via environment variable)
- **Rate Limit Per Minute**: Requests allowed per token each minute

#### API Authentication
Configure tokens to secure the RAG API via environment variables:

```bash
export MACBOT_RAG_API_TOKENS="token1,token2,token3"
```

Or set individual tokens:

```bash
export MACBOT_RAG_API_TOKEN_1="token1"
export MACBOT_RAG_API_TOKEN_2="token2"
```

Clients must include the token in an `Authorization` header:

```
Authorization: Bearer your-api-token
```

Requests missing or using invalid tokens receive `401 Unauthorized`. Exceeding
the rate limit returns `429 Too Many Requests`.

### Orchestrator
- **Host/Port**: Defaults to `127.0.0.1:8090` for control API
- **Check Interval**: How often to check service health (seconds)
- **Auto Restart**: Automatically restart failed services

### Voice Assistant Performance
- `voice_assistant.performance.transcription_cache_window_sec` (float, default `2.0`):
  - Controls how many seconds of most recent audio are hashed for the streaming transcription cache key.
  - Smaller values reduce hashing cost; larger values can improve cache reuse for slow‑changing buffers.

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

You can override configuration values using environment variables. Security-sensitive values should always be set via environment variables.

### Authentication & Security
```bash
# JWT Authentication
export MACBOT_JWT_SECRET="your-very-secure-jwt-secret-key-here"
export MACBOT_JWT_EXPIRY_HOURS="24"

# RAG API Tokens (comma-separated list)
export MACBOT_RAG_API_TOKENS="token1,token2,token3"

# Individual RAG tokens
export MACBOT_RAG_API_TOKEN_1="token1"
export MACBOT_RAG_API_TOKEN_2="token2"
```

### Model Paths
```bash
export LLAMA_MODEL_PATH="/path/to/model.gguf"
export WHISPER_MODEL_PATH="/path/to/whisper-model.bin"
export PIPER_VOICE_PATH="/path/to/piper-voice.onnx"
```

### Service Configuration
```bash
export MACBOT_WEB_PORT="3000"
export MACBOT_VOICE_PORT="8123"
export MACBOT_RAG_PORT="8081"
export MACBOT_ORCHESTRATOR_PORT="8090"
```

### Logging & Debugging
```bash
export MACBOT_LOG_LEVEL="INFO"
export MACBOT_STRUCTURED_LOGGING="true"
export MACBOT_CORRELATION_IDS="true"
export DEBUG="1"  # Enable debug mode
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

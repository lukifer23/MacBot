# MacBot API Reference

## Overview
MacBot provides several API endpoints for interacting with the system programmatically. All services run locally on your machine.

## Recent Updates
- Production-ready hardening with JWT authentication and comprehensive security
- Circuit breaker pattern for service resilience and automatic recovery
- Advanced input validation and sanitization for all endpoints
- Resource management with automatic cleanup and memory leak prevention
- Structured logging with correlation IDs for better debugging
- Zero type checker errors with comprehensive type annotations

## LLM Server (llama.cpp)
**Base URL:** `http://localhost:8080`

### OpenAI-Compatible Endpoints
- `POST /v1/chat/completions` - Generate chat completions
- `POST /v1/completions` - Generate text completions
- `GET /v1/models` - List available models

### Health Check
- `GET /health` - Server health status

## Voice Assistant API
**Base URL:** `http://localhost:8123`

### Control Endpoints
- `GET /health` - Voice assistant health status
  ```json
  {
    "status": "ok",
    "interruption_enabled": true,
    "timestamp": 1234567890.123
  }
  ```

- `POST /interrupt` - Interrupt current speech/TTS playback
  ```json
  {
    "status": "ok"
  }
  ```

- `POST /speak` - Speak text using TTS system
  ```json
  {
    "text": "Hello, this is a test message",
    "interruptible": false
  }
  ```

- `POST /mic-check` - Test microphone permissions
  ```json
  {
    "ok": true
  }
  ```

- `GET /info` - Get comprehensive voice assistant information
  ```json
  {
    "stt": {
      "impl": "whisper|whispercpp",
      "model": "models/whisper.cpp/models/ggml-base.en.bin",
      "language": "en"
    },
    "tts": {
      "engine": "piper",
      "voice": "en_US-lessac-medium",
      "voice_path": "piper_voices/en_US-lessac-medium/model.onnx",
      "engine_loaded": true,
      "speed": 1.0
    },
    "interruption": {
      "enabled": true,
      "threshold": 0.01,
      "cooldown": 0.5,
      "conversation_timeout": 30,
      "context_buffer_size": 10
    },
    "audio": {
      "sample_rate": 16000,
      "block_sec": 0.03,
      "vad_threshold": 0.01,
      "devices_default": [4, 5]
    },
    "conversation": null
  }
  ```

### New Voice & Device Endpoints
- `GET /devices` → List CoreAudio devices and current defaults
- `POST /set-output` → Set output device (index or name)
  ```json
  { "device": 5 }
  ```
- `GET /voices` → Discover voices from `piper_voices/*/model.onnx`
- `POST /set-voice` → Apply a Piper voice and persist to config
  ```json
  { "voice_path": "piper_voices/en_US-lessac-medium/model.onnx" }
  ```
- `POST /preview-voice` → Speak a short sample without changing config
  ```json
  { "text": "Hey there, how can I help?" }
  ```

### New LLM Control Endpoint
- `POST /set-llm-max-tokens` → Update max completion tokens at runtime and persist to config
  ```json
  { "max_tokens": 1024 }
  ```

## Web Dashboard API
**Base URL:** `http://localhost:3000`

### System Monitoring
- `GET /api/stats` - Real-time system statistics
  ```json
  {
    "cpu_percent": 15.2,
    "memory_percent": 45.8,
    "disk_usage": {
      "total": 1000000000000,
      "used": 500000000000,
      "free": 500000000000
    },
    "network": {
      "bytes_sent": 1024000,
      "bytes_recv": 2048000
    }
  }
  ```

## Orchestrator API
**Base URL:** `http://127.0.0.1:8090`

The orchestrator provides a control and observability API for all services. By default, it binds to `127.0.0.1` for safety.

- `GET /health` – Orchestrator health and summary
- `GET /status` – Status summary of managed processes
- `GET /services` – Machine-readable status for each managed service
- `GET /metrics` – Consolidated metrics (LLM, VA, RAG)
- `GET /pipeline-check` – Lightweight end-to-end readiness check
- `POST /service/<name>/restart` – Restart a managed service (`voice_assistant`, `rag`, `web_gui`, `llama`)

### Service Status
- `GET /api/services` - Service health and status
  ```json
  {
    "llama_server": {
      "status": "running",
      "url": "http://localhost:8080",
      "pid": 12345
    },
    "voice_assistant": {
      "status": "running",
      "pid": 12346
    },
    "rag_server": {
      "status": "running",
      "url": "http://localhost:8081"
    }
  }
  ```

### Circuit Breaker Status
- `GET /api/circuit-breakers` - Circuit breaker health and status
  ```json
  {
    "llm_server": {
      "state": "closed",
      "failure_count": 0,
      "last_failure_time": null,
      "next_retry_time": null
    },
    "rag_server": {
      "state": "closed",
      "failure_count": 0,
      "last_failure_time": null,
      "next_retry_time": null
    }
  }
  ```

### Resource Management
- `GET /api/resources` - System resource usage and limits
  ```json
  {
    "memory": {
      "used_mb": 1024,
      "available_mb": 8192,
      "percentage": 12.5
    },
    "cpu": {
      "percentage": 15.2,
      "cores": 8
    },
    "active_resources": {
      "temp_files": 3,
      "thread_pools": 2,
      "managed_processes": 5
    }
  }
  ```

### Health Monitoring
- `GET /health` - Comprehensive system health status
  ```json
  {
    "overall_status": "healthy",
    "services": {
      "llm_server": {
        "status": "healthy",
        "last_check": "2025-01-01T12:00:00Z",
        "response_time": 150,
        "consecutive_failures": 0
      },
      "rag_server": {
        "status": "healthy",
        "last_check": "2025-01-01T12:00:00Z",
        "response_time": 200,
        "consecutive_failures": 0
      },
      "web_dashboard": {
        "status": "healthy",
        "last_check": "2025-01-01T12:00:00Z",
        "response_time": 50,
        "consecutive_failures": 0
      },
      "system_resources": {
        "status": "healthy",
        "cpu_percent": 15.2,
        "memory_percent": 45.8,
        "disk_percent": 50.0
      }
    },
    "circuit_breakers": {
      "llm_server": {
        "state": "closed",
        "failure_count": 0,
        "last_failure_time": null,
        "next_retry_time": null
      },
      "rag_server": {
        "state": "closed",
        "failure_count": 0,
        "last_failure_time": null,
        "next_retry_time": null
      }
    },
    "timestamp": "2025-01-01T12:00:00Z"
  }
  ```

### Chat Interface
- `POST /api/chat` - Send chat messages
  ```json
  {
    "message": "Hello, how are you?",
    "session_id": "optional_session_id"
  }
  ```

### Document Management
- `POST /api/upload-documents` - Upload documents to RAG knowledge base
  ```json
  // FormData with files
  Content-Type: multipart/form-data
  
  files: [file1.pdf, file2.txt, ...]
  ```
  **Response:**
  ```json
  {
    "success": true,
    "message": "Successfully uploaded 2 documents",
    "files": ["document1.pdf", "document2.txt"]
  }
  ```

### WebSocket Real-Time API
**WebSocket URL:** `ws://localhost:3000`

The web dashboard provides real-time communication via WebSocket for live updates and interactive controls.

#### Connection Events
- `connect` - Client connects to WebSocket server
- `disconnect` - Client disconnects from WebSocket server

#### Chat Events
- **Send:** `chat_message`
  ```json
  {
    "message": "Hello MacBot",
    "timestamp": "2025-01-01T12:00:00Z"
  }
  ```

- **Receive:** `conversation_update`
  ```json
  {
    "type": "message",
    "data": {
      "role": "assistant",
      "content": "Hello! How can I help you?",
      "timestamp": "2025-01-01T12:00:01Z"
    }
  }
  ```

#### Voice Control Events
- **Send:** `start_voice_recording` - Start voice input
- **Send:** `stop_voice_recording` - Stop voice input
- **Receive:** `assistant_state` - `speaking_started|speaking_ended|speaking_interrupted`
- **Receive:** `conversation_update` - voice_transcription|assistant_message|user_message

Note: When `speaking_started` is received, the dashboard pauses browser mic to prevent feedback.
  ```json
  {
    "status": "recording|processing|idle",
    "message": "Voice recording started"
  }
  ```

#### Conversation Control Events
- **Send:** `interrupt_conversation` - Interrupt current conversation
- **Send:** `clear_conversation` - Clear conversation history
- **Receive:** `conversation_update`
  ```json
  {
    "type": "conversation_cleared|conversation_interrupted",
    "timestamp": "2025-01-01T12:00:00Z"
  }
  ```

#### System Monitoring Events
- **Receive:** `system_stats` (automatic broadcast every 5 seconds)
  ```json
  {
    "cpu": 15.2,
    "ram": 45.8,
    "disk": 67.3,
    "network": {
      "bytes_sent": 1024000,
      "bytes_recv": 2048000
    },
    "timestamp": "2025-01-01T12:00:00Z"
  }
  ```

## Internal APIs

These APIs are used internally by MacBot components for inter-service communication and are not exposed externally.

### Authentication Manager API
Centralized JWT token management and validation.

#### Core Methods
- `AuthenticationManager.generate_token(user_id, permissions)` - Create JWT tokens
- `AuthenticationManager.verify_token(token)` - Validate JWT tokens
- `AuthenticationManager.verify_api_key(key)` - Validate API keys

#### Configuration
```yaml
auth:
  jwt_secret: "your-secret-key"  # Set via MACBOT_JWT_SECRET
  token_expiry_hours: 24
  api_keys: ["key1", "key2"]     # Set via MACBOT_API_TOKENS
```

### Input Validation API
Comprehensive input sanitization and validation.

#### Core Methods
- `InputValidator.validate_text_input(text, max_length, required)` - Text validation
- `InputValidator.validate_audio_data(data, max_size)` - Audio data validation
- `InputValidator.validate_request_data(data, required_fields)` - Request validation
- `InputValidator.sanitize_input(text)` - XSS prevention and sanitization

#### Configuration
```yaml
validation:
  max_text_length: 10000
  max_audio_size: 10485760  # 10MB
  xss_protection: true
  sql_injection_protection: true
```

### Resource Manager API
Automatic resource cleanup and lifecycle management.

#### Core Methods
- `ResourceManager.managed_temp_file()` - Temporary file context manager
- `ResourceManager.managed_thread_pool()` - Thread pool lifecycle management
- `ResourceManager.managed_resource()` - Generic resource cleanup
- `ResourceManager.track_resource()` - Resource usage decorator

#### Configuration
```yaml
resource_management:
  temp_file_cleanup: true
  thread_pool_timeout: 30
  memory_monitoring: true
  cleanup_interval: 300
```

### Error Handler API
Structured error handling and logging.

#### Core Methods
- `ErrorHandler.handle_error(error, context)` - Structured error processing
- `ErrorHandler.log_with_context(level, message, context)` - Context-aware logging
- `ErrorHandler.get_error_context()` - Error context extraction

#### Error Severity Levels
- `CRITICAL` - System stability threatened
- `HIGH` - Feature broken but system operational
- `MEDIUM` - Degraded functionality
- `LOW` - Minor issues

### TTSManager API
The unified TTS management system provides reliable text-to-speech with interruption support.

#### Core Methods
- `TTSManager.__init__()` - Initialize with automatic engine detection
- `speak(text, interruptible=True)` - Speak text with optional interruption support
- `interrupt()` - Interrupt current speech playback

#### Engine Priority
1. **Piper** (interruptible, high quality, preferred)
2. **pyttsx3** (non-interruptible, fallback)

#### Configuration
```yaml
models:
  tts:
    piper:
      voice_path: "piper_voices/en_US-lessac-medium/model.onnx"
      speed: 1.0
```

### Message Bus API
Internal queue-based communication system for service coordination.

#### MessageBus Class
- `register_client(client_id, service_type)` - Register a service client
- `send_message(message)` - Send message to all clients
- `unregister_client(client_id)` - Remove client registration

#### MessageBusClient Class
- `start()` - Connect to message bus
- `send_message(message)` - Send message through bus
- `register_handler(type, callback)` - Register message handler
- `stop()` - Disconnect from message bus

#### Message Types
- `interruption` - Cross-service interruption signals
- `conversation_update` - Conversation state changes
- `service_status` - Service health updates

### Conversation Manager API
Manages conversation state and interruption handling.

#### Core Methods
- `start_conversation(id)` - Begin new conversation
- `interrupt_response()` - Handle interruption
- `add_user_input(text)` - Add user message
- `update_response(text, complete)` - Update AI response

#### State Synchronization
- Automatic state coordination with audio handler
- Race condition prevention for interruptions
- Thread-safe conversation tracking

## RAG Server API
**Base URL:** `http://localhost:8081`

### Knowledge Base Search
- `POST /search` - Search the knowledge base
  ```json
  {
    "query": "search term",
    "limit": 5
  }
  ```

### Document Ingestion
- `POST /ingest` - Add documents to the knowledge base
  ```json
  {
    "documents": [
      {
        "content": "Document text content",
        "metadata": {
          "title": "Document Title",
          "source": "source_url"
        }
      }
    ]
  }
  ```

## Voice Assistant Integration

### WebSocket Connection
The voice assistant can be controlled via WebSocket for real-time audio streaming.

**WebSocket URL:** `ws://localhost:8082`

### Message Format
```json
{
  "type": "audio_data",
  "data": "base64_encoded_audio",
  "sample_rate": 16000
}
```

## Configuration

### Environment Variables
- `LLAMA_MODEL_PATH` - Path to GGUF model file
- `WHISPER_MODEL_PATH` - Path to Whisper model file
- `PORT` - Port for web dashboard (default: 3000)

### Config File
See `config.yaml` for detailed configuration options.

## Error Handling

All API endpoints return standard HTTP status codes:
- `200` - Success
- `400` - Bad Request
- `404` - Not Found
- `500` - Internal Server Error

Error responses include:
```json
{
  "error": {
    "message": "Error description",
    "code": "ERROR_CODE"
  }
}
```

## Rate Limiting

- LLM requests: Limited by model inference speed
- API endpoints: No explicit rate limiting (local service)

## Authentication

MacBot implements JWT-based authentication for secure API access. Authentication is required for most endpoints.

### JWT Authentication

#### Token Generation
```bash
# Generate JWT token with permissions
curl -X POST http://localhost:8090/auth/token \
  -H "Content-Type: application/json" \
  -d '{"permissions": ["read", "write"]}'
```

#### Using Tokens
Include the JWT token in the Authorization header:
```
Authorization: Bearer <your-jwt-token>
```

### API Key Authentication (RAG Server)

The RAG server uses API key authentication via environment variables:

```bash
export MACBOT_RAG_API_TOKENS="token1,token2,token3"
```

Include the API key in requests:
```
Authorization: Bearer your-api-key
```

### Authentication Required Endpoints
- `POST /api/chat` - Chat interface
- `POST /api/llm` - Direct LLM queries
- `POST /api/assistant-speak` - TTS requests
- `POST /api/upload-documents` - Document uploads
- `POST /api/interrupt` - Conversation interruption
- `POST /service/<name>/restart` - Service management
- `GET /api/services` - Service status
- `GET /metrics` - System metrics
- `GET /pipeline-check` - Readiness checks

### Optional Authentication Endpoints
- `GET /health` - Health status (shows authenticated status if token provided)
- `GET /status` - Basic service status

### Authentication Errors
```json
{
  "error": {
    "message": "Authentication required",
    "code": "AUTH_REQUIRED"
  }
}
```
### LLM Settings API (Dashboard)
- `GET /api/llm-settings` → Current model path, context_length, temperature, threads, max_tokens
- `POST /api/set-llm-max-tokens` → Persist `models.llm.max_tokens` and nudge VA to apply at runtime

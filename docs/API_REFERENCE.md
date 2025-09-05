# MacBot API Reference

## Overview
MacBot provides several API endpoints for interacting with the system programmatically. All services run locally on your machine.

## LLM Server (llama.cpp)
**Base URL:** `http://localhost:8080`

### OpenAI-Compatible Endpoints
- `POST /v1/chat/completions` - Generate chat completions
- `POST /v1/completions` - Generate text completions
- `GET /v1/models` - List available models

### Health Check
- `GET /health` - Server health status

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
- **Receive:** `voice_status`
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

Currently, no authentication is required as all services run locally.

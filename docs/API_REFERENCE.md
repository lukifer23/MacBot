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

### Chat Interface
- `POST /api/chat` - Send chat messages
  ```json
  {
    "message": "Hello, how are you?",
    "session_id": "optional_session_id"
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

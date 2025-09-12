#!/bin/bash

# MacBot Startup Script
echo "🤖 Starting MacBot - Local Voice Assistant"
echo "========================================"

# Set environment
export PYTHONPATH="/Users/admin/Downloads/MacBot/src"
cd /Users/admin/Downloads/MacBot
source macbot_env/bin/activate

# Function to check if service is ready
check_service() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if curl -s "$url" > /dev/null 2>&1; then
            echo "✅ $name ready"
            return 0
        fi
        echo "⏳ Waiting for $name... (attempt $attempt/$max_attempts)"
        sleep 2
        ((attempt++))
    done

    echo "❌ $name failed to start"
    return 1
}

# Start LLM Server
echo "🚀 Starting LLM Server..."
./models/llama.cpp/build/bin/llama-server \
    -m models/llama.cpp/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
    --port 8080 \
    --host 127.0.0.1 \
    -ngl 999 &
LLM_PID=$!

if ! check_service "http://localhost:8080/v1/models" "LLM Server"; then
    kill $LLM_PID 2>/dev/null
    exit 1
fi

# Start RAG Server
echo "🔍 Starting RAG Server..."
python -c "
import sys
sys.path.insert(0, 'src')
from macbot.rag_server import start_rag_server
start_rag_server(host='localhost', port=8001)
" &
RAG_PID=$!

if ! check_service "http://localhost:8001/health" "RAG Server"; then
    kill $LLM_PID $RAG_PID 2>/dev/null
    exit 1
fi

# Start Web Dashboard
echo "🌐 Starting Web Dashboard..."
export FLASK_ENV=development
python -c "
import sys, os
sys.path.insert(0, 'src')
os.environ['FLASK_ENV'] = 'development'
from macbot.web_dashboard import start_dashboard
start_dashboard(host='0.0.0.0', port=3000)
" &
WEB_PID=$!

if ! check_service "http://localhost:3000" "Web Dashboard"; then
    kill $LLM_PID $RAG_PID $WEB_PID 2>/dev/null
    exit 1
fi

# Start Voice Assistant
echo "🎤 Starting Voice Assistant..."
python -c "
import sys
sys.path.insert(0, 'src')
from macbot.voice_assistant import main
main()
" &
VOICE_PID=$!

echo ""
echo "🎉 All services started successfully!"
echo "🌐 Web Dashboard: http://localhost:3000"
echo "🎤 Voice Assistant: Ready for voice input"
echo "🤖 LLM Server: http://localhost:8080"
echo "🔍 RAG Server: http://localhost:8001"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait for interrupt
trap 'echo -e "\n🛑 Stopping all services..."; kill $LLM_PID $RAG_PID $WEB_PID $VOICE_PID 2>/dev/null; exit 0' INT TERM

# Keep script alive
while true; do
    sleep 1
done

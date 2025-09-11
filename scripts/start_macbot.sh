#!/bin/bash

echo "🚀 MacBot Enhanced Voice Assistant Startup Script"
echo "=================================================="

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Please run 'make venv' first."
    exit 1
fi

# Check if models are downloaded
if [ ! -f "models/llama.cpp/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf" ]; then
    echo "❌ Qwen model not found. Please ensure the model is downloaded."
    exit 1
fi

echo "✅ Environment check passed"
echo ""

echo "Choose your startup option:"
echo "1) 🎤 Voice Assistant Only (enhanced with tools)"
echo "2) 🌐 Web GUI + Voice Assistant"
echo "3) 🎯 Full Orchestrator (manages all services)"
echo "4) 📊 Status Check"
echo "5) 🛑 Stop All Services"
echo "6) 🌐 Open Web Dashboard"
echo ""

read -p "Enter your choice (1-6): " choice

case $choice in
    1)
        echo "🎤 Starting Enhanced Voice Assistant..."
        source macbot_env/bin/activate
        make run-enhanced
        ;;
    2)
        echo "🌐 Starting Web GUI + Voice Assistant..."
        source macbot_env/bin/activate
        make run-enhanced
        ;;
    3)
        echo "🎯 Starting Full Orchestrator..."
        source macbot_env/bin/activate
        make run-orchestrator
        ;;
    4)
        echo "📊 Checking system status..."
        source macbot_env/bin/activate
        python -m src.macbot.orchestrator --status
        ;;
    5)
        echo "🛑 Stopping all services..."
        source macbot_env/bin/activate
        python -m src.macbot.orchestrator --stop
        ;;
    6)
        echo "🌐 Opening web dashboard..."
        if curl -s http://localhost:3000 > /dev/null 2>&1; then
            open http://localhost:3000
            echo "✅ Web dashboard opened in your browser"
        else
            echo "❌ Web dashboard not running. Start it first with option 3."
        fi
        ;;
    *)
        echo "❌ Invalid choice. Exiting."
        exit 1
        ;;
esac

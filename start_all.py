#!/usr/bin/env python3
"""
MacBot Startup Script - Start all services in correct order
"""
import os
import sys
import time
import subprocess
import signal
import threading
from pathlib import Path

# Set up environment
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / 'src'))

# Import MacBot modules
from macbot import config as CFG

class MacBotStarter:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.processes = {}
        self.venv_python = self.project_root / 'macbot_env' / 'bin' / 'python'

    def start_llm_server(self):
        """Start LLM server"""
        print("🚀 Starting LLM Server...")
        cmd = [
            str(self.project_root / 'models' / 'llama.cpp' / 'build' / 'bin' / 'llama-server'),
            '-m', str(self.project_root / 'models' / 'llama.cpp' / 'models' / 'Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf'),
            '--port', '8080',
            '--host', '127.0.0.1',
            '-ngl', '999'
        ]

        process = subprocess.Popen(cmd, cwd=str(self.project_root))
        self.processes['llm'] = process

        # Wait for LLM to be ready
        import requests
        for _ in range(60):
            try:
                response = requests.get('http://localhost:8080/v1/models', timeout=2)
                if response.status_code == 200:
                    print("✅ LLM Server ready")
                    return True
            except:
                pass
            time.sleep(2)

        print("❌ LLM Server failed to start")
        return False

    def start_rag_server(self):
        """Start RAG server"""
        print("🔍 Starting RAG Server...")
        cmd = [str(self.venv_python), '-c', '''
import sys
sys.path.insert(0, "src")
from macbot.rag_server import start_rag_server
start_rag_server(host="localhost", port=8001)
''']

        process = subprocess.Popen(cmd, cwd=str(self.project_root))
        self.processes['rag'] = process

        # Wait for RAG to be ready
        import requests
        for _ in range(30):
            try:
                response = requests.get('http://localhost:8001/health', timeout=1)
                if response.status_code == 200:
                    print("✅ RAG Server ready")
                    return True
            except:
                pass
            time.sleep(1)

        print("❌ RAG Server failed to start")
        return False

    def start_web_dashboard(self):
        """Start Web Dashboard"""
        print("🌐 Starting Web Dashboard...")
        cmd = [str(self.venv_python), '-c', '''
import sys, os
sys.path.insert(0, "src")
os.environ["FLASK_ENV"] = "development"
from macbot.web_dashboard import start_dashboard
start_dashboard(host="0.0.0.0", port=3000)
''']

        process = subprocess.Popen(cmd, cwd=str(self.project_root))
        self.processes['web'] = process

        # Wait for web dashboard to be ready
        import requests
        for _ in range(20):
            try:
                response = requests.get('http://localhost:3000', timeout=1)
                if response.status_code == 200:
                    print("✅ Web Dashboard ready")
                    return True
            except:
                pass
            time.sleep(1)

        print("❌ Web Dashboard failed to start")
        return False

    def start_voice_assistant(self):
        """Start Voice Assistant"""
        print("🎤 Starting Voice Assistant...")
        cmd = [str(self.venv_python), '-c', '''
import sys
sys.path.insert(0, "src")
from macbot.voice_assistant import main
main()
''']

        process = subprocess.Popen(cmd, cwd=str(self.project_root))
        self.processes['voice'] = process
        print("✅ Voice Assistant started")
        return True

    def start_all(self):
        """Start all services in order"""
        print("🤖 Starting MacBot - Local Voice Assistant")
        print("=" * 50)

        # Start services in order
        services = [
            (self.start_llm_server, "LLM Server"),
            (self.start_rag_server, "RAG Server"),
            (self.start_web_dashboard, "Web Dashboard"),
            (self.start_voice_assistant, "Voice Assistant")
        ]

        for start_func, name in services:
            if not start_func():
                print(f"❌ Failed to start {name}")
                self.stop_all()
                return False

        print("\n🎉 All services started successfully!")
        print("🌐 Web Dashboard: http://localhost:3000")
        print("🎤 Voice Assistant: Ready for voice input")
        print("🤖 LLM Server: http://localhost:8080")
        print("🔍 RAG Server: http://localhost:8001")
        print("\nPress Ctrl+C to stop all services")

        return True

    def stop_all(self):
        """Stop all services"""
        print("\n🛑 Stopping all services...")
        for name, process in self.processes.items():
            try:
                process.terminate()
                process.wait(timeout=5)
                print(f"✅ Stopped {name}")
            except subprocess.TimeoutExpired:
                process.kill()
                print(f"⚠️ Force killed {name}")

        self.processes.clear()

    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.stop_all()
        sys.exit(0)

def main():
    starter = MacBotStarter()

    # Set up signal handlers
    signal.signal(signal.SIGINT, starter.signal_handler)
    signal.signal(signal.SIGTERM, starter.signal_handler)

    try:
        if starter.start_all():
            # Keep main thread alive
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nReceived interrupt signal")
    finally:
        starter.stop_all()

if __name__ == "__main__":
    main()

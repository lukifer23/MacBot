#!/usr/bin/env python3
"""
MacBot Orchestrator - Central management for all voice assistant components
"""
import os
import sys
import time
import signal
import subprocess
import threading

# Add src/ to path for imports if run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import psutil
import yaml
import requests
from pathlib import Path
from typing import Dict, List, Optional
import logging

from .message_bus import MessageBus, start_message_bus, stop_message_bus
from .message_bus_client import MessageBusClient
from . import config as CFG

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/macbot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MacBotOrchestrator:
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'config.yaml')
        self.config_path = config_path
        self.config = self.load_config()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.threads: Dict[str, threading.Thread] = {}
        self.running = False
        
        # Message bus integration
        self.message_bus = None
        self.bus_client = None
        
        # Service status tracking
        self.service_status: Dict[str, Dict] = {}
        
        # Signal handling
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def load_config(self) -> dict:
        """Load configuration from YAML file"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                    # Merge with defaults to ensure all keys exist
                    default_config = self.get_default_config()
                    return self.merge_configs(default_config, config)
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}, using defaults")
                return self.get_default_config()
        return self.get_default_config()
    
    def merge_configs(self, default: dict, user: dict) -> dict:
        """Merge user config with defaults"""
        result = default.copy()
        for key, value in user.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self.merge_configs(result[key], value)
            else:
                result[key] = value
        return result
    
    def get_default_config(self) -> dict:
        """Default configuration if none exists"""
        return {
            'llama': {
                'server_url': 'http://localhost:8080/v1/chat/completions',
                'temperature': 0.4,
                'max_tokens': 200,
                'context_size': 4096,
                'threads': 4,
                'gpu_layers': 999
            },
            'whisper': {
                'model': 'models/whisper.cpp/models/ggml-base.en.bin',
                'language': 'en'
            },
            'tts': {
                'voice': 'af_heart',
                'speed': 1.0
            },
            'web_gui': {
                'port': 3000,
                'host': '0.0.0.0'
            },
            'rag': {
                'enabled': True,
                'vector_db': 'chromadb',
                'embedding_model': 'sentence-transformers/all-MiniLM-L6-v2'
            },
            'tools': {
                'web_search': True,
                'browser_automation': True,
                'file_operations': True
            }
        }
    
    def start_message_bus(self) -> bool:
        """Start the message bus system"""
        try:
            logger.info("Starting message bus...")
            
            # Start message bus server
            self.message_bus = start_message_bus(
                host=self.config.get('communication', {}).get('message_bus', {}).get('host', 'localhost'),
                port=self.config.get('communication', {}).get('message_bus', {}).get('port', 8082)
            )
            
            # Start orchestrator client
            self.bus_client = MessageBusClient(
                host=self.config.get('communication', {}).get('message_bus', {}).get('host', 'localhost'),
                port=self.config.get('communication', {}).get('message_bus', {}).get('port', 8082),
                service_type='orchestrator'
            )
            self.bus_client.start()
            
            # Register message handlers
            self._register_message_handlers()
            
            # Wait for connection
            timeout = 10
            start_time = time.time()
            while not self.bus_client.is_connected() and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if self.bus_client.is_connected():
                logger.info("✅ Message bus connected")
                return True
            else:
                logger.error("❌ Message bus connection failed")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start message bus: {e}")
            return False
    
    def stop_message_bus(self):
        """Stop the message bus system"""
        try:
            if self.bus_client:
                self.bus_client.stop()
                self.bus_client = None
            
            if self.message_bus:
                stop_message_bus()
                self.message_bus = None
            
            logger.info("✅ Message bus stopped")
            return True
        except Exception as e:
            logger.error(f"Failed to stop message bus: {e}")
            return False
    
    def _register_message_handlers(self):
        """Register message handlers for the orchestrator"""
        if not self.bus_client:
            return
            
        # Handle service registration
        self.bus_client.register_handler('service_registered', self._handle_service_registered)
        
        # Handle status updates
        self.bus_client.register_handler('status_update', self._handle_status_update)
        
        # Handle conversation messages
        self.bus_client.register_handler('conversation_message', self._handle_conversation_message)
        
        # Handle errors
        self.bus_client.register_handler('error', self._handle_error)
    
    async def _handle_service_registered(self, data: dict):
        """Handle service registration messages"""
        service_id = data.get('service_id')
        service_type = data.get('service_type')
        capabilities = data.get('capabilities', [])
        
        if service_id:
            self.service_status[service_id] = {
                'type': service_type,
                'capabilities': capabilities,
                'status': 'registered',
                'last_seen': time.time()
            }
            
            logger.info(f"Service registered: {service_type} ({service_id})")
    
    async def _handle_status_update(self, data: dict):
        """Handle status update messages"""
        service_id = data.get('client_id')
        status = data.get('status', {})
        
        if service_id in self.service_status:
            self.service_status[service_id].update({
                'status': status,
                'last_seen': time.time()
            })
    
    async def _handle_conversation_message(self, data: dict):
        """Handle conversation messages"""
        text = data.get('text', '')
        source = data.get('source', 'unknown')
        service_type = data.get('service_type', 'unknown')
        
        logger.info(f"Conversation from {service_type}: {text[:100]}...")
        
        # Broadcast to other services
        if self.bus_client:
            self.bus_client.send_message({
                'type': 'conversation_broadcast',
                'original_source': service_type,
                'text': text,
                'timestamp': time.time()
            })
    
    async def _handle_error(self, data: dict):
        """Handle error messages"""
        error = data.get('error', 'Unknown error')
        service_type = data.get('service_type', 'unknown')
        
        logger.error(f"Error from {service_type}: {error}")
        
        # Could implement error recovery logic here
    
    def start_llama_server(self) -> bool:
        """Start the llama.cpp server"""
        try:
            cmd = [
                'make', 'run-llama'
            ]
            logger.info("Starting llama.cpp server...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.getcwd()
            )
            self.processes['llama'] = process
            
            # Wait for server to be ready
            for _ in range(30):  # 30 second timeout
                try:
                    # llama.cpp doesn't have a health endpoint, check if it's listening
                    from . import config as _cfg
                    response = requests.get(_cfg.get_llm_models_endpoint(), timeout=1)
                    if response.status_code == 200:
                        logger.info("✅ llama.cpp server ready")
                        return True
                except:
                    time.sleep(1)
            
            logger.error("❌ llama.cpp server failed to start")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start llama server: {e}")
            return False
    
    def start_web_gui(self) -> bool:
        """Start the web GUI dashboard"""
        try:
            cmd = ['python', '-m', 'macbot.web_dashboard']
            
            logger.info("Starting web GUI...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.processes['web_gui'] = process
            
            # Wait for web GUI to be ready
            for _ in range(15):  # 15 second timeout
                try:
                    response = requests.get(f"http://localhost:{self.config['web_gui']['port']}", timeout=1)
                    if response.status_code == 200:
                        logger.info("✅ Web GUI ready")
                        return True
                except:
                    time.sleep(1)
            
            logger.error("❌ Web GUI failed to start")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start web GUI: {e}")
            return False
    
    def start_rag_service(self) -> bool:
        """Start the RAG service"""
        if not self.config['rag']['enabled']:
            logger.info("RAG service disabled in config")
            return True
            
        try:
            cmd = ['python', '-m', 'macbot.rag_server']
            logger.info("Starting RAG service...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.processes['rag'] = process
            
            # Wait for RAG service to be ready
            for _ in range(15):  # 15 second timeout
                try:
                    host, port = CFG.get_rag_host_port()
                    response = requests.get(f"http://{host}:{port}/health", timeout=1)
                    if response.status_code == 200:
                        logger.info("✅ RAG service ready")
                        return True
                except:
                    time.sleep(1)
            
            logger.error("❌ RAG service failed to start")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start RAG service: {e}")
            return False
    
    def start_voice_assistant(self) -> bool:
        """Start the enhanced voice assistant"""
        try:
            cmd = ['python', '-m', 'macbot.voice_assistant']
            logger.info("Starting enhanced voice assistant...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.processes['voice_assistant'] = process
            logger.info("✅ Voice assistant started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start voice assistant: {e}")
            return False
    
    def check_web_dependencies(self) -> bool:
        """Check if web GUI dependencies are installed"""
        try:
            import flask
            import psutil
            import requests
            return True
        except ImportError:
            return False
    
    def install_web_dependencies(self):
        """Install web GUI dependencies"""
        try:
            subprocess.run([
                sys.executable, '-m', 'pip', 'install',
                'flask', 'psutil', 'requests'
            ], check=True)
            logger.info("Web GUI dependencies installed")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install web dependencies: {e}")
    
    def monitor_system(self):
        """Monitor system resources"""
        while self.running:
            try:
                # Get system stats
                cpu_percent = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                
                # Log system stats
                logger.info(f"System: CPU {cpu_percent}% | RAM {memory.percent}% | Disk {disk.percent}%")
                
                # Check process health
                self.check_process_health()
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"System monitoring error: {e}")
                time.sleep(30)
    
    def check_process_health(self):
        """Check if all processes are still running"""
        for name, process in self.processes.items():
            if process.poll() is not None:
                logger.warning(f"Process {name} died, restarting...")
                self.restart_process(name)
    
    def restart_process(self, name: str):
        """Restart a specific process"""
        if name == 'llama':
            self.start_llama_server()
        elif name == 'web_gui':
            self.start_web_gui()
        elif name == 'rag':
            self.start_rag_service()
        elif name == 'voice_assistant':
            self.start_voice_assistant()
    
    def start_all(self) -> bool:
        """Start all services"""
        logger.info("🚀 Starting MacBot Orchestrator...")
        
        # Start services in order
        services = [
            ('llama', self.start_llama_server),
            ('web_gui', self.start_web_gui),
            ('rag', self.start_rag_service),
            ('voice_assistant', self.start_voice_assistant)
        ]
        
        for name, start_func in services:
            if not start_func():
                if name == 'rag':
                    logger.warning(f"RAG service failed to start, continuing without it")
                    continue
                else:
                    logger.error(f"Failed to start {name}, stopping all services")
                    self.stop_all()
                    return False
        
        # Start system monitoring
        self.running = True
        monitor_thread = threading.Thread(target=self.monitor_system, daemon=True)
        monitor_thread.start()
        self.threads['monitor'] = monitor_thread
        
        logger.info("🎉 All services started successfully!")
        host, port = CFG.get_web_dashboard_host_port()
        logger.info(f"🌐 Web GUI: http://{host}:{port}")
        logger.info(f"🤖 Voice Assistant: Ready")
        logger.info(f"🔍 RAG Service: {'Ready' if self.config.get('rag', {}).get('enabled', True) else 'Disabled'}")
        
        return True
    
    def stop_all(self):
        """Stop all services"""
        logger.info("🛑 Stopping all services...")
        self.running = False
        
        # Stop all processes
        for name, process in self.processes.items():
            try:
                process.terminate()
                process.wait(timeout=5)
                logger.info(f"Stopped {name}")
            except subprocess.TimeoutExpired:
                process.kill()
                logger.warning(f"Force killed {name}")
            except Exception as e:
                logger.error(f"Error stopping {name}: {e}")
        
        self.processes.clear()
        logger.info("All services stopped")
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop_all()
        sys.exit(0)
    
    def status(self):
        """Show status of all services"""
        print("\n🔍 MacBot Status Report")
        print("=" * 50)
        
        for name, process in self.processes.items():
            if process.poll() is None:
                print(f"✅ {name}: Running (PID: {process.pid})")
            else:
                print(f"❌ {name}: Stopped")
        
        # System stats
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        print(f"\n💻 System: CPU {cpu_percent}% | RAM {memory.percent}%")
        
        # Service URLs
        host, port = CFG.get_web_dashboard_host_port()
        print(f"\n🌐 Web GUI: http://{host}:{port}")
        print(f"🤖 LLM API: {CFG.get_llm_models_endpoint().rsplit('/v1',1)[0]}")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='MacBot Orchestrator')
    parser.add_argument('--config', default='config.yaml', help='Configuration file path')
    parser.add_argument('--status', action='store_true', help='Show status and exit')
    parser.add_argument('--stop', action='store_true', help='Stop all services and exit')
    
    args = parser.parse_args()
    
    orchestrator = MacBotOrchestrator(args.config)
    
    if args.status:
        orchestrator.status()
        return
    
    if args.stop:
        orchestrator.stop_all()
        return
    
    try:
        if orchestrator.start_all():
            logger.info("Press Ctrl+C to stop all services")
            # Keep main thread alive
            while orchestrator.running:
                time.sleep(1)
        else:
            logger.error("Failed to start services")
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    finally:
        orchestrator.stop_all()

if __name__ == "__main__":
    main()

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
import uuid

# Add src/ to path for imports if run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import psutil
import yaml
import requests
from pathlib import Path
from typing import Dict, List, Optional
import logging
from logging.handlers import RotatingFileHandler
from .logging_utils import setup_logger

from .message_bus import MessageBus, start_message_bus, stop_message_bus
from .message_bus_client import MessageBusClient
from .health_monitor import get_health_monitor
from .message_bus_server import start_message_bus_server
from . import config as CFG

# Configure logging (unified)
logger = setup_logger("macbot.orchestrator", "logs/macbot.log")

class MacBotOrchestrator:
    def __init__(self, config_path: Optional[str] = None):
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
        self.ws_bus_server = None
        
        # Health monitoring integration
        self.health_monitor = get_health_monitor()
        
        # Service status tracking
        self.service_status: Dict[str, Dict] = {}
        
        # Signal handling
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        # Control HTTP server (status/health)
        self.control_thread: Optional[threading.Thread] = None
    
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
            logger.info("Starting in-process message bus...")
            
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

    def start_ws_message_bus(self) -> bool:
        """Start the WebSocket message bus server (cross-process)."""
        try:
            host = CFG.get('communication.message_bus.host', '127.0.0.1')
            port = int(CFG.get('communication.message_bus.port', 8082))
            logger.info(f"Starting WS message bus on ws://{host}:{port} ...")
            self.ws_bus_server = start_message_bus_server(host=host, port=port)
            logger.info("✅ WS message bus started")
            return True
        except Exception as e:
            logger.warning(f"Failed to start WS message bus: {e}")
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

            if self.ws_bus_server:
                try:
                    self.ws_bus_server.stop()
                except Exception:
                    pass
                self.ws_bus_server = None

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
            # Get model path and parameters from config
            model_path = CFG.get_llm_model_path()
            if not os.path.exists(model_path):
                logger.error(f"LLM model not found at: {model_path}")
                return False
            
            # Build command to start llama-server directly
            # Determine threads: if config is <=0, compute a higher value favoring performance
            cfg_threads = CFG.get_llm_threads()
            try:
                logical = os.cpu_count() or 1
            except Exception:
                logical = 1
            try:
                import psutil as _ps
                physical = _ps.cpu_count(logical=False) or (logical // 2) or 1
            except Exception:
                physical = (logical // 2) or 1
            # Double physical cores but don't exceed logical
            computed_threads = cfg_threads if cfg_threads and cfg_threads > 0 else min(logical, max(1, physical * 2))

            cmd = [
                os.path.join(os.getcwd(), 'models', 'llama.cpp', 'build', 'bin', 'llama-server'),
                '-m', model_path,
                '-c', str(CFG.get_llm_context_length()),
                '-t', str(computed_threads),
                '-ngl', '999',  # offload max layers to Metal
                '--port', '8080',
                '--host', '127.0.0.1'
            ]
            
            logger.info("Starting llama.cpp server...")
            logger.info(f"Command: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.getcwd()
            )
            self.processes['llama'] = process
            
            # Wait for server to be ready
            for _ in range(60):  # 60 second timeout for model loading
                try:
                    response = requests.get('http://localhost:8080/v1/models', timeout=2)
                    if response.status_code == 200:
                        # Try to extract model info for helpful logs
                        model_name = None
                        try:
                            data = response.json()
                            models = data.get('data') or data.get('models') or []
                            if isinstance(models, list) and models:
                                m0 = models[0]
                                model_name = m0.get('id') or m0.get('name') or m0.get('model')
                        except Exception:
                            pass

                        # Memory stats for llama process
                        mem_info = None
                        try:
                            p = psutil.Process(process.pid)
                            rss = p.memory_info().rss
                            mem_pct = p.memory_percent()
                            mem_info = (rss, mem_pct)
                        except Exception:
                            mem_info = None

                        if model_name and mem_info:
                            rss_mb = mem_info[0] / (1024*1024)
                            logger.info(f"✅ llama.cpp server ready | model={model_name} | ctx={CFG.get_llm_context_length()} | threads={computed_threads} | RSS={rss_mb:.1f} MB | mem%={mem_info[1]:.2f}")
                        elif model_name:
                            logger.info(f"✅ llama.cpp server ready | model={model_name} | ctx={CFG.get_llm_context_length()} | threads={computed_threads}")
                        else:
                            logger.info("✅ llama.cpp server ready")
                        return True
                except (requests.exceptions.RequestException, ValueError) as e:
                    logger.debug(f"LLM server not ready yet: {e}")
                    time.sleep(1)
            
            logger.error("❌ llama.cpp server failed to start")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start llama server: {e}")
            return False
    
    def start_web_gui(self) -> bool:
        """Start the web GUI dashboard"""
        try:
            # Use virtual environment if available
            venv_python = os.path.join(os.path.dirname(__file__), '..', '..', 'macbot_env', 'bin', 'python')
            if os.path.exists(venv_python):
                cmd = [venv_python, '-m', 'macbot.web_dashboard']
                env = os.environ.copy()
                env['PYTHONPATH'] = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
            else:
                cmd = ['python', '-m', 'macbot.web_dashboard']
                env = None
            
            logger.info("Starting web GUI...")
            process = subprocess.Popen(
                cmd,
                stdout=None,  # Don't capture stdout for web dashboard
                stderr=None,  # Don't capture stderr for web dashboard
                env=env,
                cwd=os.path.join(os.path.dirname(__file__), '..', '..')
            )
            self.processes['web_gui'] = process
            
            # Wait for web GUI to be ready
            for _ in range(30):  # 30 second timeout (increased for dependencies)
                try:
                    host, port = CFG.get_web_dashboard_host_port()
                    response = requests.get(f"http://{host}:{port}", timeout=1)
                    if response.status_code == 200:
                        logger.info("✅ Web GUI ready")
                        return True
                except (requests.exceptions.RequestException, ValueError) as e:
                    logger.debug(f"Web GUI not ready yet: {e}")
                    time.sleep(1)
            
            # Check if process is still running
            if process.poll() is not None:
                logger.error(f"Web GUI process exited with code {process.returncode}")
                logger.error("❌ Web GUI failed to start")
                return False
            
        except Exception as e:
            logger.error(f"Failed to start web GUI: {e}")
            return False
    
    def start_rag_service(self) -> bool:
        """Start the RAG service"""
        try:
            # Use virtual environment if available
            venv_python = os.path.join(os.path.dirname(__file__), '..', '..', 'macbot_env', 'bin', 'python')
            if os.path.exists(venv_python):
                cmd = [venv_python, '-m', 'macbot.rag_server']
                env = os.environ.copy()
                env['PYTHONPATH'] = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
            else:
                cmd = ['python', '-m', 'macbot.rag_server']
                env = None
            logger.info("Starting RAG service...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=os.path.join(os.path.dirname(__file__), '..', '..')
            )
            self.processes['rag'] = process
            
            # Wait for RAG service to be ready
            for _ in range(30):  # 30 second timeout (increased for model loading)
                try:
                    host, port = CFG.get_rag_host_port()
                    response = requests.get(f"http://{host}:{port}/health", timeout=1)
                    if response.status_code == 200:
                        logger.info("✅ RAG service ready")
                        return True
                except (requests.exceptions.RequestException, ValueError) as e:
                    logger.debug(f"RAG service not ready yet: {e}")
                    time.sleep(1)
            
            logger.error("❌ RAG service failed to start")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start RAG service: {e}")
            return False
    
    def start_voice_assistant(self) -> bool:
        """Start the enhanced voice assistant"""
        try:
            # Use virtual environment if available
            venv_python = os.path.join(os.path.dirname(__file__), '..', '..', 'macbot_env', 'bin', 'python')
            if os.path.exists(venv_python):
                cmd = [venv_python, '-m', 'macbot.voice_assistant']
                env = os.environ.copy()
                env['PYTHONPATH'] = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
            else:
                cmd = ['python', '-m', 'macbot.voice_assistant']
                env = None
            logger.info("Starting enhanced voice assistant...")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=os.path.join(os.path.dirname(__file__), '..', '..')
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
            ('ws_bus', self.start_ws_message_bus),
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
        
        # Start health monitoring
        self.health_monitor.start_monitoring()
        logger.info("✅ Health monitoring started")
        
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

        # Start control server
        try:
            self.start_control_server()
        except Exception as e:
            logger.warning(f"Failed to start orchestrator control server: {e}")

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

    def start_control_server(self):
        """Start a lightweight HTTP server exposing /health and /status"""
        from flask import Flask, jsonify
        from . import config as CFG

        app = Flask("macbot_orchestrator")

        @app.route('/health')
        def health():
            req_id = str(uuid.uuid4())
            logger.info(f"orc_req id={req_id} path=/health")
            return jsonify({'status':'ok','timestamp': time.time(), 'req_id': req_id})

        @app.route('/status')
        def status():
            req_id = str(uuid.uuid4())
            procs = {}
            for name, proc in self.processes.items():
                procs[name] = {
                    'running': proc.poll() is None,
                    'pid': proc.pid if proc and proc.poll() is None else None
                }
            logger.info(f"orc_req id={req_id} path=/status processes={list(procs.keys())}")
            return jsonify({'processes': procs, 'req_id': req_id})

        @app.route('/metrics')
        def metrics():
            req_id = str(uuid.uuid4())
            data = {'req_id': req_id, 'timestamp': time.time()}
            # LLM metrics
            llm = {}
            try:
                models_ep = CFG.get_llm_models_endpoint()
                r = requests.get(models_ep, timeout=2)
                if r.ok:
                    j = r.json()
                    models = j.get('data') or j.get('models') or []
                    if isinstance(models, list) and models:
                        m0 = models[0]
                        llm['model'] = m0.get('id') or m0.get('name') or m0.get('model')
                llm['context'] = CFG.get_llm_context_length()
                llm['threads'] = CFG.get_llm_threads()
                llm['gpu_layers'] = 999
                # process RSS
                p = self.processes.get('llama')
                if p and p.poll() is None:
                    try:
                        rss = psutil.Process(p.pid).memory_info().rss
                        llm['rss_mb'] = round(rss / (1024*1024), 1)
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"metrics llm error: {e}")
            data['llm'] = llm

            # Voice assistant info
            va = {}
            try:
                vhost, vport = CFG.get_voice_assistant_host_port()
                r = requests.get(f"http://{vhost}:{vport}/info", timeout=2)
                if r.ok:
                    va = r.json()
            except Exception as e:
                logger.debug(f"metrics va error: {e}")
            data['voice_assistant'] = va

            # RAG stats
            rag = {}
            try:
                rhost, rport = CFG.get_rag_host_port()
                r = requests.get(f"http://{rhost}:{rport}/api/stats", timeout=2)
                if r.ok:
                    rag = r.json()
            except Exception as e:
                logger.debug(f"metrics rag error: {e}")
            data['rag'] = rag

            return jsonify(data)

        @app.route('/pipeline-check')
        def pipeline_check():
            """Lightweight end-to-end readiness check across components."""
            results = {'timestamp': time.time()}
            # LLM quick chat
            try:
                chat_ep = CFG.get_llm_chat_endpoint()
                payload = {
                    "model": "local",
                    "messages": [{"role":"user","content":"ping"}],
                    "max_tokens": 8,
                    "temperature": 0.1
                }
                r = requests.post(chat_ep, json=payload, timeout=5)
                results['llm'] = {'ok': r.ok, 'code': r.status_code}
            except Exception as e:
                results['llm'] = {'ok': False, 'error': str(e)}

            # STT presence
            try:
                from . import config as _C
                stt_bin = _C.get_stt_bin()
                stt_model = _C.get_stt_model()
                results['stt'] = {
                    'bin_exists': os.path.exists(stt_bin),
                    'model_exists': os.path.exists(stt_model)
                }
            except Exception as e:
                results['stt'] = {'ok': False, 'error': str(e)}

            # Voice assistant info (STT/TTS)
            try:
                vh, vp = CFG.get_voice_assistant_host_port()
                r = requests.get(f"http://{vh}:{vp}/info", timeout=3)
                if r.ok:
                    info = r.json()
                    results['voice_assistant'] = {
                        'stt': info.get('stt') or {},
                        'tts': info.get('tts') or {}
                    }
                    results['tts'] = {'engine': (info.get('tts') or {}).get('engine'), 'ok': (info.get('tts') or {}).get('engine') is not None}
                else:
                    results['voice_assistant'] = {}
                    results['tts'] = {'ok': False, 'code': r.status_code}
            except Exception as e:
                results['voice_assistant'] = {}
                results['tts'] = {'ok': False, 'error': str(e)}

            # RAG
            try:
                rh, rp = CFG.get_rag_host_port()
                r = requests.get(f"http://{rh}:{rp}/health", timeout=3)
                results['rag'] = {'ok': r.ok, 'code': r.status_code}
            except Exception as e:
                results['rag'] = {'ok': False, 'error': str(e)}

            results['overall'] = all([
                results.get('llm',{}).get('ok', True),
                results.get('stt',{}).get('bin_exists', True),
                results.get('stt',{}).get('model_exists', True),
                results.get('tts',{}).get('ok', True),
                results.get('rag',{}).get('ok', True),
            ])
            return jsonify(results)

        host, port = CFG.get("services.orchestrator.host", "0.0.0.0"), int(CFG.get("services.orchestrator.port", 8090))

        def run():
            try:
                app.run(host=host, port=port, debug=False, use_reloader=False)
            except Exception as e:
                logger.warning(f"Control server failed: {e}")

        self.control_thread = threading.Thread(target=run, daemon=True)
        self.control_thread.start()
        logger.info(f"Orchestrator control server on http://{host}:{port}")
    
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

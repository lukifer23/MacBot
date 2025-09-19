#!/usr/bin/env python3
"""
MacBot Flask App Base - Common Flask application patterns
"""
import os
import time
import uuid
from typing import Dict, Any, Optional, Callable
from flask import Flask, jsonify, request
from .logging_utils import setup_logger

logger = setup_logger("macbot.flask_app", "logs/flask_app.log")


class MacBotFlaskApp:
    """Base class for MacBot Flask applications with common patterns"""
    
    def __init__(self, name: str, enable_cors: bool = True):
        self.app = Flask(name)
        self.name = name
        
        # Configure CORS if requested
        if enable_cors:
            try:
                from flask_cors import CORS
                CORS(self.app, origins=[
                    "http://localhost:3000", 
                    "http://127.0.0.1:3000", 
                    "http://192.168.1.38:3000"
                ])
            except ImportError:
                logger.warning("flask-cors not available, CORS disabled")
        
        # Add common routes
        self._add_common_routes()
    
    def _add_common_routes(self):
        """Add common routes to all MacBot Flask apps"""
        
        @self.app.route('/health')
        def health():
            req_id = str(uuid.uuid4())
            logger.info(f"{self.name}_req id={req_id} path=/health")
            return jsonify({
                'status': 'ok',
                'service': self.name,
                'timestamp': time.time(),
                'req_id': req_id
            })
        
        @self.app.route('/info')
        def info():
            """Basic service information"""
            return jsonify({
                'service': self.name,
                'status': 'running',
                'timestamp': time.time()
            })
    
    def add_route(self, rule: str, methods: Optional[list] = None, **options):
        """Decorator to add routes to the Flask app"""
        def decorator(f: Callable):
            self.app.add_url_rule(rule, f.__name__, f, methods=methods, **options)
            return f
        return decorator
    
    def run(self, host: str = '0.0.0.0', port: int = 3000, debug: bool = False, **kwargs):
        """Run the Flask app with common configuration"""
        try:
            logger.info(f"Starting {self.name} on http://{host}:{port}")
            self.app.run(host=host, port=port, debug=debug, use_reloader=False, **kwargs)
        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning(f"Port {port} already in use for {self.name}")
            else:
                logger.error(f"Failed to start {self.name}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to start {self.name}: {e}")
            raise


__all__ = ["MacBotFlaskApp"]

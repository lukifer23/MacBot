#!/usr/bin/env python3
"""
MacBot Message Bus Client - Client library for connecting to the message bus
"""
import json
import logging
import threading
import queue
import time
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Import message_bus with proper relative import to avoid circular imports
try:
    from .message_bus import message_bus
except ImportError:
    # Fallback for when imported as a standalone module
    message_bus = None

class MessageBusClient:
    """Client for connecting to the MacBot message bus"""

    def __init__(self, host: str = "localhost", port: int = 8082, service_type: str = "unknown"):
        self.host = host
        self.port = port
        self.service_type = service_type
        self.client_id: Optional[str] = None
        self.message_queue: Optional[queue.Queue] = None
        self.running = False
        self.connected = False
        self.message_handlers: Dict[str, List[Callable]] = {}

        # Threading
        self.thread: Optional[threading.Thread] = None

        # Connection management
        self.reconnect_interval = 5

    def start(self):
        """Start the message bus client"""
        self.running = True
        self.client_id = f"{self.service_type}_{int(time.time())}"
        
        if message_bus:
            self.message_queue = message_bus.register_client(self.client_id, self.service_type)
            self.connected = True
            logger.info(f"Message bus client connected for {self.service_type}")
            
            # Start message processing thread
            self.thread = threading.Thread(target=self._process_messages, daemon=True)
            self.thread.start()
        else:
            logger.error("Message bus not available")

    def stop(self):
        """Stop the message bus client"""
        self.running = False
        
        if message_bus and self.client_id:
            message_bus.unregister_client(self.client_id)
        
        if self.thread:
            self.thread.join(timeout=5)
        
        self.connected = False
        logger.info(f"Message bus client stopped for {self.service_type}")

    def send_message(self, message: dict):
        """Send a message through the bus"""
        if not self.connected or not message_bus:
            logger.warning("Not connected to message bus, message not sent")
            return
        
        # Add metadata
        enriched_message = {
            **message,
            'sender_id': self.client_id,
            'sender_type': self.service_type,
            'timestamp': datetime.now().isoformat()
        }
        
        message_bus.send_message(enriched_message)

    def is_connected(self) -> bool:
        """Check if client is connected"""
        return self.connected

    def _process_messages(self):
        """Process incoming messages"""
        while self.running and self.message_queue:
            try:
                # Non-blocking get with timeout
                message = self.message_queue.get(timeout=0.1)
                
                # Handle message
                self._handle_message(message)
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Message processing error: {e}")

    def _handle_message(self, message: dict):
        """Handle incoming message"""
        message_type = message.get('type', 'unknown')
        
        # Call registered handlers
        if message_type in self.message_handlers:
            for handler in self.message_handlers[message_type]:
                try:
                    handler(message)
                except Exception as e:
                    logger.error(f"Error in message handler: {e}")
        
        # Log message
        logger.debug(f"Received message: {message_type}")

    def register_handler(self, message_type: str, handler: Callable):
        """Register a message handler"""
        if message_type not in self.message_handlers:
            self.message_handlers[message_type] = []
        self.message_handlers[message_type].append(handler)

    def unregister_handler(self, message_type: str, handler: Callable):
        """Unregister a message handler"""
        if message_type in self.message_handlers:
            try:
                self.message_handlers[message_type].remove(handler)
            except ValueError:
                pass


# Export the main class
__all__ = ['MessageBusClient']

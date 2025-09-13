#!/usr/bin/env python3
"""
MacBot Message Bus - Simplified real-time communication system for all services
"""
import logging
import threading
import queue
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime

from .logging_utils import setup_logger

logger = setup_logger("macbot.message_bus", "logs/message_bus.log")

class MessageBus:
    """Simplified message bus for real-time service communication"""

    def __init__(self, host: str = "localhost", port: int = 8082):
        self.host = host
        self.port = port
        self.clients: Dict[str, Dict] = {}  # client_id -> {service_type, last_seen, message_queue}
        self.message_handlers: Dict[str, List[Callable]] = {}
        self.running = False
        self.thread = None
        self.message_queue = queue.Queue()

    def start(self):
        """Start the message bus"""
        self.running = True
        self.thread = threading.Thread(target=self._process_messages, daemon=True)
        self.thread.start()
        logger.info(f"Message bus started on {self.host}:{self.port}")

    def stop(self):
        """Stop the message bus"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Message bus stopped")

    def register_client(self, client_id: str, service_type: str) -> queue.Queue:
        """Register a new client and return its message queue"""
        client_queue = queue.Queue()
        self.clients[client_id] = {
            'service_type': service_type,
            'last_seen': datetime.now(),
            'message_queue': client_queue
        }
        logger.info(f"Client registered: {service_type} ({client_id})")
        return client_queue

    def unregister_client(self, client_id: str):
        """Unregister a client"""
        if client_id in self.clients:
            del self.clients[client_id]
            logger.info(f"Client unregistered: {client_id}")

    def send_message(self, message: dict, target_client: Optional[str] = None, target_service: Optional[str] = None):
        """Send a message to specific client or service type"""
        if target_client and target_client in self.clients:
            # Send to specific client
            self.clients[target_client]['message_queue'].put(message)
        elif target_service:
            # Send to all clients of specific service type
            sent = 0
            for client_id, client_info in self.clients.items():
                if client_info['service_type'] == target_service:
                    client_info['message_queue'].put(message)
                    sent += 1
            if sent == 0:
                logger.warning(f"No clients found for service type: {target_service}")
        else:
            # Broadcast to all clients
            for client_id, client_info in self.clients.items():
                client_info['message_queue'].put(message)

    def broadcast(self, message: dict, exclude_client: Optional[str] = None):
        """Broadcast message to all clients except excluded one"""
        for client_id, client_info in self.clients.items():
            if client_id != exclude_client:
                client_info['message_queue'].put(message)

    def publish(self, message: dict, target_client: Optional[str] = None, target_service: Optional[str] = None):
        """Enqueue a message for asynchronous dispatch"""
        self.message_queue.put({
            'message': message,
            'target_client': target_client,
            'target_service': target_service
        })

    # Alias for backward compatibility / alternative naming
    enqueue = publish

    def _process_messages(self):
        """Process messages in the background"""
        while self.running:
            try:
                item = self.message_queue.get(timeout=0.1)
                message = item.get('message')
                target_client = item.get('target_client')
                target_service = item.get('target_service')
                self.send_message(message, target_client=target_client, target_service=target_service)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Message processing error: {e}")

    def get_clients_by_service_type(self, service_type: str) -> List[str]:
        """Get all client IDs for a specific service type"""
        return [client_id for client_id, info in self.clients.items() 
                if info['service_type'] == service_type]

    def get_service_status(self) -> Dict[str, Any]:
        """Get status of all services"""
        status = {}
        for service_type in set(info['service_type'] for info in self.clients.values()):
            clients = self.get_clients_by_service_type(service_type)
            status[service_type] = {
                'count': len(clients),
                'clients': clients,
                'last_seen': max((info['last_seen'] for info in self.clients.values() 
                                if info['service_type'] == service_type), default=None)
            }
        return status


# Global message bus instance
message_bus = None


def start_message_bus(host: str = "localhost", port: int = 8082) -> MessageBus:
    """Start the global message bus"""
    global message_bus
    message_bus = MessageBus(host, port)
    message_bus.start()
    return message_bus


def stop_message_bus():
    """Stop the global message bus"""
    global message_bus
    if message_bus:
        message_bus.stop()


# Export the main classes and functions
__all__ = ['MessageBus', 'start_message_bus', 'stop_message_bus']

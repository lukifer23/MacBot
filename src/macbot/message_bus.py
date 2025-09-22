#!/usr/bin/env python3
"""
MacBot Message Bus - Simplified real-time communication system for all services
"""
import logging
import threading
import queue
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime, timedelta
from enum import Enum

from .logging_utils import setup_logger

logger = setup_logger("macbot.message_bus", "logs/message_bus.log")

class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """Circuit breaker for service communication"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 30, expected_exception: type[Exception] = Exception):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.success_count = 0
        self.attempt_count = 0

    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        if self.state == CircuitBreakerState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitBreakerState.HALF_OPEN
                self.attempt_count = 0
                logger.info("Circuit breaker half-open, attempting reset")
            else:
                raise Exception("Circuit breaker is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            # Check if the exception matches our expected type
            if isinstance(e, self.expected_exception):
                self._on_failure()
            raise e

    def _should_attempt_reset(self):
        """Check if enough time has passed to attempt reset"""
        if self.last_failure_time is None:
            return True
        return datetime.now() - self.last_failure_time > timedelta(seconds=self.recovery_timeout)

    def _on_success(self):
        """Handle successful call"""
        self.failure_count = 0
        self.success_count += 1
        if self.state == CircuitBreakerState.HALF_OPEN:
            if self.attempt_count >= 3:  # Require 3 successes to fully close
                self.state = CircuitBreakerState.CLOSED
                logger.info("Circuit breaker closed after successful attempts")
        self.attempt_count = 0

    def _on_failure(self):
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        self.attempt_count += 1

        if self.state == CircuitBreakerState.HALF_OPEN:
            # Reset to open on first failure in half-open state
            self.state = CircuitBreakerState.OPEN
            logger.warning("Circuit breaker reopened after failure in half-open state")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")

    def get_state(self):
        """Get current circuit breaker state"""
        return {
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None
        }

class MessageBus:
    """Simplified message bus for real-time service communication"""

    def __init__(self, host: str = "localhost", port: int = 8082, max_queue_size: int = 1000):
        self.host = host
        self.port = port
        self.max_queue_size = max_queue_size
        self.clients: Dict[str, Dict] = {}  # client_id -> {service_type, last_seen, message_queue}
        self.message_handlers: Dict[str, List[Callable]] = {}
        self.running = False
        self.thread = None
        self.message_queue = queue.Queue(maxsize=max_queue_size)

        # Circuit breakers for different service types
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.circuit_breaker_lock = threading.Lock()

        # Backpressure management
        self.dropped_messages_total = 0
        self.queue_pressure_threshold = max_queue_size * 0.8  # Start backpressure at 80% capacity

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
        client_queue = queue.Queue(maxsize=self.max_queue_size)
        self.clients[client_id] = {
            'service_type': service_type,
            'last_seen': datetime.now(),
            'message_queue': client_queue,
            'dropped_messages': 0
        }
        logger.info(f"Client registered: {service_type} ({client_id})")
        return client_queue

    def unregister_client(self, client_id: str):
        """Unregister a client"""
        if client_id in self.clients:
            del self.clients[client_id]
            logger.info(f"Client unregistered: {client_id}")

    def touch_client(self, client_id: str) -> None:
        """Refresh client's last_seen timestamp"""
        if client_id in self.clients:
            self.clients[client_id]['last_seen'] = datetime.now()

    def _try_send_to_client(self, client_id: str, message: dict) -> bool:
        """Try to send a message to a client with circuit breaker and backpressure handling"""
        if client_id not in self.clients:
            return False

        client_info = self.clients[client_id]
        client_queue = client_info['message_queue']

        try:
            # Check if client queue is full (backpressure)
            if client_queue.qsize() >= client_queue.maxsize * 0.9:  # 90% full
                client_info['dropped_messages'] += 1
                self.dropped_messages_total += 1
                if client_info['dropped_messages'] % 50 == 1:  # Log more frequently for high pressure
                    logger.warning(f"Client {client_id} queue at {client_queue.qsize()}/{client_queue.maxsize}, dropped {client_info['dropped_messages']} messages total")
                return False

            # Try to put message with timeout to avoid blocking
            client_queue.put_nowait(message)
            self.touch_client(client_id)
            return True
        except queue.Full:
            # Queue is full, drop the message and track it
            client_info['dropped_messages'] += 1
            self.dropped_messages_total += 1
            if client_info['dropped_messages'] % 50 == 1:  # Log every 50th drop
                logger.warning(f"Client {client_id} queue full, dropped {client_info['dropped_messages']} messages")
            return False

    def send_message(self, message: dict, target_client: Optional[str] = None, target_service: Optional[str] = None):
        """Send a message to specific client or service type with circuit breaker protection"""
        if target_client and target_client in self.clients:
            # Send to specific client
            self._try_send_to_client(target_client, message)
        elif target_service:
            # Send to all clients of specific service type with circuit breaker
            circuit_breaker = self._get_circuit_breaker(target_service)

            def _send_to_service():
                sent = 0
                failed_clients = 0
                for client_id, client_info in self.clients.items():
                    if client_info['service_type'] == target_service:
                        try:
                            if self._try_send_to_client(client_id, message):
                                sent += 1
                            else:
                                failed_clients += 1
                        except Exception as e:
                            logger.warning(f"Failed to send to client {client_id}: {e}")
                            failed_clients += 1

                if failed_clients > 0:
                    logger.warning(f"Failed to send to {failed_clients} clients of service type {target_service}")

                if sent == 0:
                    raise Exception(f"No active clients found for service type: {target_service}")
                return sent

            try:
                circuit_breaker.call(_send_to_service)
            except Exception as e:
                logger.warning(f"Service {target_service} circuit breaker prevented message: {e}")

        else:
            # Broadcast to all clients
            for client_id in self.clients:
                self._try_send_to_client(client_id, message)

    def broadcast(self, message: dict, exclude_client: Optional[str] = None):
        """Broadcast message to all clients except excluded one"""
        for client_id in self.clients:
            if client_id != exclude_client:
                self._try_send_to_client(client_id, message)

    def _get_circuit_breaker(self, service_type: str) -> CircuitBreaker:
        """Get or create circuit breaker for service type"""
        with self.circuit_breaker_lock:
            if service_type not in self.circuit_breakers:
                self.circuit_breakers[service_type] = CircuitBreaker(
                    failure_threshold=5,
                    recovery_timeout=30
                )
            return self.circuit_breakers[service_type]

    def publish(self, message: dict, target_client: Optional[str] = None, target_service: Optional[str] = None):
        """Enqueue a message for asynchronous dispatch with backpressure handling"""
        try:
            # Check if message bus queue is under pressure
            if self.message_queue.qsize() >= self.queue_pressure_threshold:
                self.dropped_messages_total += 1
                if self.dropped_messages_total % 10 == 1:  # Log every 10th drop
                    logger.warning(f"Message bus queue under pressure ({self.message_queue.qsize()}/{self.max_queue_size}), dropped {self.dropped_messages_total} messages total")
                return False

            self.message_queue.put_nowait({
                'message': message,
                'target_client': target_client,
                'target_service': target_service
            })
            return True
        except queue.Full:
            self.dropped_messages_total += 1
            if self.dropped_messages_total % 10 == 1:
                logger.warning(f"Message bus queue full, dropped {self.dropped_messages_total} messages")
            return False

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
        status: Dict[str, Any] = {}
        for client_id, info in self.clients.items():
            service_type = info['service_type']
            if service_type not in status:
                status[service_type] = {
                    'count': 0,
                    'clients': {},
                    'last_seen': None,
                }

            service_entry = status[service_type]
            service_entry['count'] += 1
            service_entry['clients'][client_id] = {
                'last_seen': info['last_seen'],
                'queue_size': info['message_queue'].qsize(),
                'dropped_messages': info['dropped_messages']
            }

            if (service_entry['last_seen'] is None or
                    info['last_seen'] > service_entry['last_seen']):
                service_entry['last_seen'] = info['last_seen']

        return status

    def get_circuit_breaker_status(self) -> Dict[str, Dict]:
        """Get status of all circuit breakers"""
        with self.circuit_breaker_lock:
            return {service_type: cb.get_state() for service_type, cb in self.circuit_breakers.items()}

    def get_queue_status(self) -> Dict[str, Any]:
        """Get queue and backpressure status"""
        return {
            'queue_size': self.message_queue.qsize(),
            'max_queue_size': self.max_queue_size,
            'queue_pressure_threshold': self.queue_pressure_threshold,
            'under_pressure': self.message_queue.qsize() >= self.queue_pressure_threshold,
            'dropped_messages_total': self.dropped_messages_total
        }

    def clear_dropped_messages_count(self) -> None:
        """Clear the dropped messages counter"""
        self.dropped_messages_total = 0
        # Also clear per-client counters
        for client_info in self.clients.values():
            client_info['dropped_messages'] = 0


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

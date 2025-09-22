"""
MacBot Centralized Error Handling and Exception Management
"""
import os
import sys
import time
import traceback
import logging
from typing import Dict, Any, Optional, Callable, Type
from functools import wraps
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .logging_utils import setup_logger

logger = setup_logger("macbot.error_handler", "logs/macbot.log")

class ErrorSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class ErrorContext:
    """Context information for error tracking"""
    component: str
    operation: str
    user_id: Optional[str] = None
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

class ErrorHandler:
    """Centralized error handling and reporting system"""

    def __init__(self):
        self.error_count = 0
        self.error_history = []
        self.max_history_size = 1000
        self.error_handlers: Dict[Type[Exception], Callable] = {}
        self.circuit_breakers: Dict[str, Dict] = {}

    def register_error_handler(self, exception_type: Type[Exception], handler: Callable) -> None:
        """Register a custom error handler for a specific exception type"""
        self.error_handlers[exception_type] = handler

    def handle_error(self, error: Exception, context: ErrorContext, severity: ErrorSeverity = ErrorSeverity.MEDIUM) -> Dict[str, Any]:
        """Handle an error with context and severity"""
        error_id = f"ERR_{int(time.time() * 1000000)}"
        self.error_count += 1

        # Get error details
        error_details = {
            'error_id': error_id,
            'type': error.__class__.__name__,
            'message': str(error),
            'traceback': traceback.format_exc(),
            'context': {
                'component': context.component,
                'operation': context.operation,
                'user_id': context.user_id,
                'request_id': context.request_id,
                'session_id': context.session_id,
                'metadata': context.metadata
            },
            'severity': severity.value,
            'timestamp': context.timestamp.isoformat(),
            'count': self.error_count
        }

        # Check for custom handler
        if error.__class__ in self.error_handlers:
            try:
                return self.error_handlers[error.__class__](error, context, severity)
            except Exception as handler_error:
                logger.error(f"Error in custom handler for {error.__class__.__name__}: {handler_error}")

        # Standard error handling
        self._log_error(error_details, severity)

        # Store in history
        self._add_to_history(error_details)

        # Check for circuit breaker patterns
        self._check_circuit_breaker(context.component, severity)

        return error_details

    def _log_error(self, error_details: Dict[str, Any], severity: ErrorSeverity) -> None:
        """Log error with appropriate level"""
        log_message = f"[{error_details['error_id']}] {error_details['type']}: {error_details['message']}"

        if severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message, extra={'error_details': error_details})
        elif severity == ErrorSeverity.HIGH:
            logger.error(log_message, extra={'error_details': error_details})
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message, extra={'error_details': error_details})
        else:
            logger.info(log_message, extra={'error_details': error_details})

    def _add_to_history(self, error_details: Dict[str, Any]) -> None:
        """Add error to history, removing old entries if needed"""
        self.error_history.append(error_details)
        if len(self.error_history) > self.max_history_size:
            self.error_history = self.error_history[-self.max_history_size:]

    def _check_circuit_breaker(self, component: str, severity: ErrorSeverity) -> None:
        """Check if we should trigger circuit breaker for component"""
        if component not in self.circuit_breakers:
            self.circuit_breakers[component] = {
                'failure_count': 0,
                'last_failure_time': None,
                'state': 'closed'
            }

        cb = self.circuit_breakers[component]
        now = datetime.now()

        # Reset counter if enough time has passed
        if cb['last_failure_time'] and (now - cb['last_failure_time']).seconds > 300:  # 5 minutes
            cb['failure_count'] = 0
            cb['state'] = 'closed'

        # Increment failure count
        if severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            cb['failure_count'] += 1
            cb['last_failure_time'] = now

            # Check if we should open circuit
            if cb['failure_count'] >= 5 and cb['state'] == 'closed':
                cb['state'] = 'open'
                logger.critical(f"Circuit breaker OPEN for component {component} after {cb['failure_count']} failures")

    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics"""
        return {
            'total_errors': self.error_count,
            'recent_errors': len(self.error_history),
            'circuit_breaker_states': self.circuit_breakers.copy(),
            'error_types': self._get_error_type_counts()
        }

    def _get_error_type_counts(self) -> Dict[str, int]:
        """Get counts of error types"""
        counts = {}
        for error in self.error_history[-100:]:  # Last 100 errors
            error_type = error['type']
            counts[error_type] = counts.get(error_type, 0) + 1
        return counts

    def clear_error_history(self) -> None:
        """Clear error history"""
        self.error_history.clear()
        self.error_count = 0

def get_error_handler() -> ErrorHandler:
    """Get or create error handler instance"""
    if not hasattr(get_error_handler, '_instance'):
        get_error_handler._instance = ErrorHandler()
    return get_error_handler._instance

def handle_error(error: Exception, component: str, operation: str, severity: ErrorSeverity = ErrorSeverity.MEDIUM, **context_kwargs) -> Dict[str, Any]:
    """Convenience function to handle errors"""
    context = ErrorContext(component=component, operation=operation, **context_kwargs)
    handler = get_error_handler()
    return handler.handle_error(error, context, severity)

def with_error_handling(component: str, operation: str, severity: ErrorSeverity = ErrorSeverity.MEDIUM):
    """Decorator to add error handling to functions"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_details = handle_error(e, component, operation, severity)
                # Re-raise with additional context
                raise type(e)(f"Error in {component}.{operation}: {str(e)}") from e
        return wrapper
    return decorator

@contextmanager
def error_context(component: str, operation: str, severity: ErrorSeverity = ErrorSeverity.MEDIUM):
    """Context manager for error handling"""
    try:
        yield
    except Exception as e:
        handle_error(e, component, operation, severity)
        raise

class MacBotException(Exception):
    """Base exception for MacBot-specific errors"""

    def __init__(self, message: str, component: str = "unknown", operation: str = "unknown", **kwargs):
        super().__init__(message)
        self.component = component
        self.operation = operation
        self.context = kwargs

class AuthenticationError(MacBotException):
    """Authentication-related errors"""
    pass

class ConfigurationError(MacBotException):
    """Configuration-related errors"""
    pass

class ServiceUnavailableError(MacBotException):
    """Service unavailable errors"""
    pass

class ValidationError(MacBotException):
    """Input validation errors"""
    pass

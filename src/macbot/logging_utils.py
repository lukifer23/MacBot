#!/usr/bin/env python3
"""
Unified logging utility for MacBot services.

Provides setup_logger(name, logfile) to configure a rotating file handler and
console handler with consistent formatting. Idempotent: reuses existing handlers
if already configured for the logger.

Supports both traditional and structured JSON logging.
"""
from __future__ import annotations

import logging
import os
import json
import uuid
import traceback
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, Optional
from datetime import datetime


def setup_logger(name: str, logfile: str, level: int = logging.INFO, structured: bool = False) -> logging.Logger:
    """Create or return a configured logger with rotating file + console handlers.

    Args:
        name: Logger name
        logfile: Log file path
        level: Log level
        structured: Whether to use structured JSON logging
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Ensure logs directory exists
    try:
        logdir = os.path.dirname(logfile)
        if logdir and not os.path.exists(logdir):
            os.makedirs(logdir, exist_ok=True)
    except Exception:
        pass

    if structured:
        # Use JSON formatter for structured logging
        fmt = JSONFormatter()
    else:
        # Traditional formatter
        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # File handler
    try:
        fh = RotatingFileHandler(logfile, maxBytes=2_000_000, backupCount=3)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # If file handler fails, rely on console handler only
        pass

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger

class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""

    def __init__(self, include_request_id: bool = True):
        super().__init__()
        self.include_request_id = include_request_id

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        # Base log entry
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        # Add request ID if available
        if self.include_request_id and hasattr(record, 'request_id'):
            log_entry['request_id'] = record.request_id

        # Add error details if available
        if hasattr(record, 'error_details') and record.error_details:
            log_entry['error_details'] = record.error_details

        # Add any extra fields
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
                          'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
                          'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
                          'thread', 'threadName', 'processName', 'process', 'message',
                          'request_id', 'error_details']:
                log_entry[key] = value

        return json.dumps(log_entry, default=str)

def log_with_context(logger: logging.Logger, level: int, message: str, **context) -> None:
    """Log message with additional context"""
    # Generate request ID if not provided
    request_id = context.get('request_id', str(uuid.uuid4())[:8])

    # Create a log record with context
    record = logging.LogRecord(
        name=logger.name,
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None
    )

    # Add context to record
    record.request_id = request_id
    for key, value in context.items():
        setattr(record, key, value)

    logger.handle(record)

def log_error_with_context(logger: logging.Logger, error: Exception, message: str = None, **context) -> str:
    """Log error with context and return error ID"""
    if message is None:
        message = f"Error in {context.get('component', 'unknown')}: {str(error)}"

    error_id = f"ERR_{int(time.time() * 1000000)}"
    context['error_id'] = error_id
    context['error_type'] = error.__class__.__name__
    context['error_message'] = str(error)

    # Add traceback if available
    if hasattr(error, '__traceback__'):
        context['traceback'] = traceback.format_exception(type(error), error, error.__traceback__)

    log_with_context(logger, logging.ERROR, message, **context)
    return error_id


__all__ = ["setup_logger", "JSONFormatter", "log_with_context", "log_error_with_context"]


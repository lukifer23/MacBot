#!/usr/bin/env python3
"""
Unified logging utility for MacBot services.

Provides setup_logger(name, logfile) to configure a rotating file handler and
console handler with consistent formatting. Idempotent: reuses existing handlers
if already configured for the logger.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name: str, logfile: str, level: int = logging.INFO) -> logging.Logger:
    """Create or return a configured logger with rotating file + console handlers."""
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


__all__ = ["setup_logger"]


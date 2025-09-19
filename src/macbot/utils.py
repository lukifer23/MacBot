#!/usr/bin/env python3
"""
MacBot Utilities - Common utility functions
"""
import os
import sys
from pathlib import Path


def setup_path() -> None:
    """Setup Python path for MacBot modules.
    
    This should be called at the beginning of any MacBot module
    that needs to import other MacBot modules.
    """
    # Add src/ to path if not already there
    src_path = os.path.join(os.path.dirname(__file__), '..', '..')
    src_path = os.path.abspath(src_path)
    
    if src_path not in sys.path:
        sys.path.insert(0, src_path)


def get_project_root() -> Path:
    """Get the project root directory as a Path object"""
    return Path(__file__).parent.parent.parent


def get_config_path() -> Path:
    """Get the config file path"""
    return get_project_root() / "config" / "config.yaml"


def get_logs_dir() -> Path:
    """Get the logs directory path"""
    logs_dir = get_project_root() / "logs"
    logs_dir.mkdir(exist_ok=True)
    return logs_dir


__all__ = ["setup_path", "get_project_root", "get_config_path", "get_logs_dir"]

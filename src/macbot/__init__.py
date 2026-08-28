"""
MacBot - Local AI Voice Assistant for macOS

A comprehensive offline AI assistant with voice interface,
web dashboard, and native macOS tool integration.
"""

__version__ = "2.0.0"
__author__ = "MacBot Team"
__email__ = "info@macbot.local"


def main():
    """Keep the public entry point without importing the CLI during package import."""
    from .cli import main as run

    return run()


__all__ = ["main"]

#!/usr/bin/env python3
"""
MacBot CLI - Command Line Interface for MacBot
"""
import argparse
import sys
import os
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="MacBot - Local AI Voice Assistant for macOS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  macbot orchestrator    # Start the orchestrator
  macbot dashboard       # Start the web dashboard
  macbot rag             # Start the RAG server
  macbot voice           # Start the voice assistant
        """
    )

    parser.add_argument(
        'command',
        choices=['orchestrator', 'dashboard', 'rag', 'voice'],
        help='Command to run'
    )

    parser.add_argument(
        '--config',
        default='config/config.yaml',
        help='Path to configuration file'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )

    args = parser.parse_args()

    # Set debug mode
    if args.debug:
        os.environ['DEBUG'] = '1'

    try:
        if args.command == 'orchestrator':
            import orchestrator
            # The orchestrator module runs when imported
        elif args.command == 'dashboard':
            import web_dashboard
            # The web_dashboard module runs when imported
        elif args.command == 'rag':
            import rag_server
            # The rag_server module runs when imported
        elif args.command == 'voice':
            import voice_assistant
            # The voice_assistant module runs when imported
    except ImportError as e:
        print(f"Error importing module: {e}")
        print("Make sure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()

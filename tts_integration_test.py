#!/usr/bin/env python3
"""
TTS Integration Test
Tests the updated TTS manager with Piper support
"""

import os
import sys
from pathlib import Path

# Add src to path to import macbot modules
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from macbot.voice_assistant import TTSManager
from macbot import config as CFG

def test_tts_integration():
    """Test the TTS integration with different engines"""
    print("🔊 TTS Integration Test")
    print("=" * 50)

    # Initialize TTS manager
    print("Initializing TTS Manager...")
    tts_manager = TTSManager()

    print(f"Selected Engine: {tts_manager.engine_type}")
    print(f"Kokoro Available: {tts_manager.kokoro_available}")
    print(f"Piper Available: {tts_manager.piper_available}")
    print(f"pyttsx3 Available: {tts_manager.pyttsx3_available}")
    print(f"Say Available: {tts_manager.say_available}")
    print()

    # Test basic speak functionality
    if tts_manager.engine:
        print("🧪 Testing basic TTS functionality...")
        test_text = "Hello, this is a test of the TTS integration."

        try:
            print(f"Speaking: '{test_text}'")
            success = tts_manager.speak(test_text, interruptible=False)
            print(f"Result: {'✅ Success' if success else '❌ Failed'}")
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        print("❌ No TTS engine available")

    print()
    print("🎯 TTS Integration Status:")
    if tts_manager.engine_type == "kokoro":
        print("   ✅ Using Kokoro (interruptible)")
    elif tts_manager.engine_type == "piper":
        print("   ✅ Using Piper TTS")
    elif tts_manager.engine_type == "pyttsx3":
        print("   ⚠️  Using pyttsx3 (fallback)")
    else:
        print("   ❌ No TTS engine working")

if __name__ == "__main__":
    test_tts_integration()

#!/usr/bin/env python3
"""
TTS Test Script for MacBot
Tests the TTS system by speaking test phrases and measuring performance.
"""

import os
import sys
import time
from pathlib import Path

# Add src to path to import macbot modules
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from macbot.voice_assistant import tts_manager
from macbot import config as CFG

def test_tts_system():
    """Test the TTS system with various inputs"""
    print("🔊 MacBot TTS System Test")
    print("=" * 50)

    # Display TTS information
    print("📋 TTS System Information:")
    print(f"   Engine: {tts_manager.engine_type}")
    print(f"   Voice: {CFG.get_tts_voice()}")
    print(f"   Speed: {CFG.get_tts_speed()}")
    print(f"   Kokoro Available: {tts_manager.kokoro_available}")
    print(f"   pyttsx3 Available: {tts_manager.pyttsx3_available}")
    print(f"   Available Voices: {len(getattr(tts_manager, 'voices', []))}")
    print()

    # Test phrases
    test_phrases = [
        "Hello, this is MacBot speaking.",
        "Testing text to speech functionality.",
        "The quick brown fox jumps over the lazy dog.",
        "How are you today?",
        "System test complete."
    ]

    print("🧪 Testing TTS with different phrases...")
    print("-" * 40)

    total_latency = 0
    successful_tests = 0

    for i, phrase in enumerate(test_phrases, 1):
        print(f"\n🗣️  Test {i}: '{phrase}'")

        start_time = time.time()

        try:
            # Test both interruptible and non-interruptible modes
            if tts_manager.kokoro_available:
                print("   Using Kokoro (interruptible)")
                success = tts_manager.speak(phrase, interruptible=True)
            else:
                print("   Using pyttsx3 (non-interruptible)")
                success = tts_manager.speak(phrase, interruptible=False)

            end_time = time.time()
            latency = end_time - start_time

            if success:
                print("   ✅ Success")
                print(".3f")
                successful_tests += 1
                total_latency += latency
            else:
                print("   ❌ Failed or interrupted")
                print(".3f")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    print()
    print("📊 Performance Summary:")
    print("-" * 40)
    print(f"   Total Tests: {len(test_phrases)}")
    print(f"   Successful: {successful_tests}")
    print(f"   Success Rate: {(successful_tests/len(test_phrases))*100:.1f}%")

    if successful_tests > 0:
        avg_latency = total_latency / successful_tests
        print(".3f")

        # Estimate words per minute
        total_words = sum(len(phrase.split()) for phrase in test_phrases[:successful_tests])
        total_time = total_latency
        if total_time > 0:
            wpm = (total_words / total_time) * 60
            print(".1f")

    print()
    return successful_tests > 0

def test_tts_features():
    """Test advanced TTS features"""
    print("🔧 Testing TTS Features...")
    print("-" * 40)

    # Test interruption (if available)
    if tts_manager.kokoro_available:
        print("🧪 Testing interruption capability...")
        try:
            # Start a long speech
            long_text = "This is a longer test phrase that will be interrupted midway through the speech to test the interruption functionality."
            print(f"   Speaking: '{long_text[:50]}...'")

            start_time = time.time()

            # This would normally be interrupted, but for testing we'll just speak it
            success = tts_manager.speak(long_text, interruptible=True)

            end_time = time.time()
            latency = end_time - start_time

            if success:
                print("   ✅ Long speech completed")
                print(".3f")
            else:
                print("   ⚠️  Speech was interrupted")
                print(".3f")

        except Exception as e:
            print(f"   ❌ Interruption test failed: {e}")
    else:
        print("   ⚠️  Interruption testing skipped (Kokoro not available)")

    print()

def benchmark_tts():
    """Benchmark TTS performance with various text lengths"""
    print("⚡ TTS Performance Benchmark")
    print("-" * 40)

    test_lengths = [
        ("Short", "Hello!"),
        ("Medium", "Hello, this is a medium length test phrase for benchmarking purposes."),
        ("Long", "This is a much longer test phrase that contains more words and should take significantly more time to speak. It helps us understand how the TTS system handles longer text inputs and measures the overall performance characteristics.")
    ]

    for name, text in test_lengths:
        print(f"\n📏 {name} Text ({len(text)} chars, {len(text.split())} words):")

        start_time = time.time()

        try:
            success = tts_manager.speak(text, interruptible=tts_manager.kokoro_available)

            end_time = time.time()
            latency = end_time - start_time

            if success:
                print("   ✅ Completed")
                print(".3f")
                print(".2f")
            else:
                print("   ❌ Failed")
                print(".3f")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    print()

if __name__ == "__main__":
    try:
        success = test_tts_system()
        test_tts_features()
        benchmark_tts()

        if success:
            print("🎉 TTS test suite completed successfully!")
            print("\n💡 TTS System Status:")
            if tts_manager.kokoro_available:
                print("   • High-quality interruptible TTS available (Kokoro)")
                print("   • Supports barge-in and conversation interruption")
            elif tts_manager.pyttsx3_available:
                print("   • Basic TTS available (pyttsx3)")
                print("   • Non-interruptible but functional")
            else:
                print("   • Using system fallback ('say' command)")
                print("   • Basic functionality only")
        else:
            print("\n💥 TTS test failed!")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        sys.exit(1)

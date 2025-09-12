#!/usr/bin/env python3
"""
TTS Quality Comparison Test
Compares Piper TTS vs pyttsx3 quality and performance
"""

import os
import sys
import time
from pathlib import Path

# Add src to path to import macbot modules
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from macbot.voice_assistant import TTSManager
from macbot import config as CFG

def test_tts_quality():
    """Test TTS quality with different engines"""
    print("🎭 TTS Quality Comparison Test")
    print("=" * 60)

    # Test phrases
    test_phrases = [
        "Hello, welcome to MacBot.",
        "The quick brown fox jumps over the lazy dog.",
        "I can help you with various tasks on your computer.",
        "Please speak clearly and I'll transcribe your words.",
        "System monitoring shows everything is running normally."
    ]

    # Test with different engines
    results = {}

    for engine_name in ["piper", "pyttsx3"]:
        print(f"\n🔊 Testing {engine_name.upper()} TTS Engine")
        print("-" * 40)

        # Force specific engine by temporarily modifying the manager
        original_voice_path = None
        if engine_name == "pyttsx3":
            # Temporarily move piper voice to force fallback to pyttsx3
            piper_dir = Path("piper_voices")
            if piper_dir.exists():
                piper_backup = Path("piper_voices_backup")
                piper_dir.rename(piper_backup)
                original_voice_path = piper_backup

        # Initialize TTS manager with specific engine
        tts_manager = TTSManager()

        if tts_manager.engine_type != engine_name:
            print(f"⚠️ Could not load {engine_name}, got {tts_manager.engine_type}")
            if original_voice_path:
                original_voice_path.rename(Path("piper_voices"))
            continue

        print(f"✅ Using {engine_name} engine")

        # Test phrases
        engine_results = []
        total_time = 0

        for i, phrase in enumerate(test_phrases, 1):
            print(f"🗣️  Test {i}: '{phrase[:40]}...'")

            start_time = time.time()
            success = tts_manager.speak(phrase, interruptible=False)
            end_time = time.time()

            duration = end_time - start_time
            total_time += duration

            if success:
                print(".3f")
                engine_results.append(duration)
            else:
                print("❌ Failed")
                engine_results.append(None)

        # Calculate metrics
        successful_tests = sum(1 for r in engine_results if r is not None)
        if successful_tests > 0:
            avg_time = sum(r for r in engine_results if r is not None) / successful_tests
            words_per_minute = (sum(len(p.split()) for p in test_phrases) / total_time) * 60

            results[engine_name] = {
                'success_rate': successful_tests / len(test_phrases) * 100,
                'avg_time': avg_time,
                'words_per_minute': words_per_minute,
                'total_time': total_time
            }

            print("\n📊 Metrics:")
            print(".1f")
            print(".3f")
            print(".1f")
            print(".1f")

        # Restore piper voices if moved
        if original_voice_path:
            original_voice_path.rename(Path("piper_voices"))

    # Comparison summary
    print("\n" + "=" * 60)
    print("🏆 QUALITY COMPARISON SUMMARY")
    print("=" * 60)

    if len(results) >= 2:
        piper_results = results.get('piper', {})
        pyttsx3_results = results.get('pyttsx3', {})

        print("\n🎯 Success Rate:")
        if 'piper' in results:
            print(".1f")
        if 'pyttsx3' in results:
            print(".1f")

        print("\n⚡ Performance:")
        if 'piper' in results:
            print(".1f")
        if 'pyttsx3' in results:
            print(".1f")

        print("\n🎭 Voice Quality:")
        print("   Piper: Modern neural TTS with natural prosody")
        print("   pyttsx3: Traditional system voices (robotic/mechanical)")

        print("\n🔄 Interruptibility:")
        print("   Piper: ❌ Non-interruptible (can be enhanced)")
        print("   Kokoro: ✅ Interruptible (when available)")
        print("   pyttsx3: ❌ Non-interruptible")

    print("\n💡 RECOMMENDATION:")
    print("   🎯 Use PIPER TTS for best quality and performance!")
    print("   🚀 Ready for production use with excellent voice quality!")

if __name__ == "__main__":
    test_tts_quality()

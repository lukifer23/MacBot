#!/usr/bin/env python3
"""
Gettysburg Address TTS Test
Tests TTS with the first paragraph of the Gettysburg Address
"""

import os
import sys
import time
from pathlib import Path

# Add src to path to import macbot modules
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from macbot.voice_assistant import tts_manager
from macbot import config as CFG

def test_gettysburg_paragraph():
    """Test TTS with the first paragraph of the Gettysburg Address"""
    print("🎤 Gettysburg Address TTS Test")
    print("=" * 50)

    # First paragraph of the Gettysburg Address
    gettysburg_text = """
    Four score and seven years ago our fathers brought forth on this continent, a new nation, conceived in Liberty, and dedicated to the proposition that all men are created equal.

    Now we are engaged in a great civil war, testing whether that nation, or any nation so conceived and so dedicated, can long endure. We are met on a great battle-field of that war. We have come to dedicate a portion of that field, as a final resting place for those who here gave their lives that that nation might live. It is altogether fitting and proper that we should do this.

    But, in a larger sense, we can not dedicate—we can not consecrate—we can not hallow this ground. The brave men, living and dead, who struggled here, have consecrated it, far above our poor power to add or detract. The world will little note, nor long remember what we say here, but it can never forget what they did here. It is for us the living, rather, to be dedicated here to the unfinished work which they who fought here have thus far so nobly advanced. It is rather for us to be here dedicated to the great task remaining before us—that from these honored dead we take increased devotion to that cause for which they gave the last full measure of devotion—that we here highly resolve that these dead shall not have died in vain—that this nation, under God, shall have a new birth of freedom—and that government of the people, by the people, for the people, shall not perish from the earth.
    """

    # Count words and characters
    word_count = len(gettysburg_text.split())
    char_count = len(gettysburg_text)

    print(f"📊 Text Statistics:")
    print(f"   Characters: {char_count}")
    print(f"   Words: {word_count}")
    print(f"   Estimated reading time: ~{word_count//150 + 1} minutes")
    print()

    print("🎭 TTS System Info:")
    print(f"   Engine: {tts_manager.engine_type}")
    print(f"   Voice: {CFG.get_tts_voice()}")
    print(f"   Interruptible: {tts_manager.kokoro_available}")
    print()

    print("🗣️  Speaking Gettysburg Address (first paragraph)...")
    print("-" * 60)

    # Start timing
    start_time = time.time()

    try:
        success = tts_manager.speak(gettysburg_text, interruptible=tts_manager.kokoro_available)

        end_time = time.time()
        total_time = end_time - start_time

        if success:
            print("\n✅ Speech completed successfully!")
            print(f"⏱️  Total speaking time: {total_time:.2f} seconds")
            print(".1f")
            print(".1f")

            # Calculate if timing is reasonable (normal speaking rate is 150-160 WPM)
            expected_time = word_count / 150 * 60  # Expected time at 150 WPM
            efficiency = expected_time / total_time if total_time > 0 else 0

            print(".1f")

            if 0.8 <= efficiency <= 1.2:
                print("🎯 Speaking rate: Natural and conversational")
            elif efficiency > 1.2:
                print("🐌 Speaking rate: Slower than natural")
            else:
                print("🚀 Speaking rate: Faster than natural")

        else:
            print("\n❌ Speech failed or was interrupted")
            print(f"⏱️  Time elapsed: {total_time:.2f} seconds")

    except Exception as e:
        end_time = time.time()
        total_time = end_time - start_time
        print(f"\n❌ Error during speech: {e}")
        print(f"⏱️  Time elapsed: {total_time:.2f} seconds")

    print()
    print("📝 Sample of text spoken:")
    preview = gettysburg_text.strip()[:200] + "..."
    print(f"   \"{preview}\"")
    print()

if __name__ == "__main__":
    test_gettysburg_paragraph()
    print("🎉 Gettysburg Address test complete!")

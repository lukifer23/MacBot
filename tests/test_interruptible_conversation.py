#!/usr/bin/env python3
"""Test script for interruptible conversation system."""

import sys
import os
import pytest

pytest.skip("requires audio hardware", allow_module_level=True)

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.audio_interrupt import AudioInterruptHandler
from macbot.conversation_manager import ConversationManager, ConversationState
import numpy as np
import time

def test_audio_interruption():
    """Test basic audio interruption functionality"""
    print("🧪 Testing Audio Interruption System...")

    # Create audio handler
    handler = AudioInterruptHandler(sample_rate=24000)

    # Generate test audio (1 second of 440Hz sine wave)
    duration = 1.0
    frequency = 440.0
    t = np.linspace(0, duration, int(24000 * duration), False)
    audio = np.sin(frequency * 2 * np.pi * t).astype(np.float32)

    print("▶️  Playing test audio...")
    success = handler.play_audio(audio)
    print(f"✅ Audio playback {'completed' if success else 'was interrupted'}")

    return success

def test_conversation_manager():
    """Test conversation state management"""
    print("\n🧪 Testing Conversation Manager...")

    manager = ConversationManager()

    # Test state transitions
    print("📝 Testing conversation states...")

    # Start conversation
    conv_id = manager.start_conversation()
    print(f"📊 Started conversation: {conv_id}")

    # Add user input
    manager.add_user_input("Hello MacBot")
    print("📝 Added user input")

    # Start response
    manager.start_response()
    print("🎤 Started response")

    # Simulate interruption
    manager.interrupt_response()
    print("⏹️  Interrupted response")

    # Check if we can resume
    buffered = manager.resume_response()
    print(f"📊 Buffered response available: {buffered is not None}")

    # Complete response
    manager.complete_response()
    print("✅ Completed response")

    # Check history
    history = manager.get_recent_history()
    print(f"📚 Conversation history: {len(history)} messages")

    return len(history) > 0

def test_integration():
    """Test integrated audio interruption with conversation management"""
    print("\n🧪 Testing Integrated System...")

    # Create components
    audio_handler = AudioInterruptHandler(sample_rate=24000)
    conversation_manager = ConversationManager()

    # Register callback
    def on_state_change(context):
        print(f"🔄 State changed to: {context.current_state.value}")
        if context.current_state == ConversationState.INTERRUPTED:
            audio_handler.interrupt_playback()

    conversation_manager.register_state_callback(on_state_change)

    # Generate longer test audio (3 seconds)
    duration = 3.0
    frequency = 440.0
    t = np.linspace(0, duration, int(24000 * duration), False)
    audio = np.sin(frequency * 2 * np.pi * t).astype(np.float32)

    print("▶️  Starting integrated test...")

    # Start conversation
    conv_id = conversation_manager.start_conversation()
    conversation_manager.add_user_input("Test message")
    conversation_manager.start_response()

    # Play audio (should complete normally)
    success = audio_handler.play_audio(audio)

    # Complete response
    conversation_manager.complete_response()

    print(f"✅ Integrated test {'completed successfully' if success else 'was interrupted'}")

    return success

def main():
    """Run all tests"""
    print("🚀 MacBot Interruptible Conversation System Test Suite")
    print("=" * 60)

    try:
        # Test individual components
        audio_test = test_audio_interruption()
        conv_test = test_conversation_manager()
        integration_test = test_integration()

        # Summary
        print("\n" + "=" * 60)
        print("📊 Test Results:")
        print(f"   Audio Interruption: {'✅ PASS' if audio_test else '❌ FAIL'}")
        print(f"   Conversation Manager: {'✅ PASS' if conv_test else '❌ FAIL'}")
        print(f"   Integration Test: {'✅ PASS' if integration_test else '❌ FAIL'}")

        all_passed = audio_test and conv_test and integration_test
        print(f"\n🎯 Overall: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")

        return 0 if all_passed else 1

    except Exception as e:
        print(f"❌ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())

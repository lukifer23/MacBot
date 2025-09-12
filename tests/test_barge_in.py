import threading
import time
import numpy as np
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from macbot.conversation_manager import ConversationManager, ConversationState

SAMPLE_RATE = 24000


def test_basic_conversation_manager():
    """Test basic conversation manager functionality without threading"""
    cm = ConversationManager()
    
    # Test basic conversation start
    conv_id = cm.start_conversation()
    assert conv_id is not None
    assert cm.current_context is not None
    assert cm.current_context.current_state == ConversationState.IDLE
    
    # Test state updates
    cm.update_state(ConversationState.LISTENING)
    assert cm.current_context.current_state == ConversationState.LISTENING
    
    cm.update_state(ConversationState.PROCESSING)
    assert cm.current_context.current_state == ConversationState.PROCESSING
    
    # Test response handling
    cm.start_response("test response")
    assert cm.current_context.current_state == ConversationState.SPEAKING
    assert cm.current_context.response_state.value == "streaming"
    
    cm.update_response("test response updated")
    assert cm.current_context.ai_response == "test response updated"
    
    # Test interrupt
    cm.interrupt_response()
    assert cm.current_context.current_state == ConversationState.INTERRUPTED
    assert cm.current_context.response_state.value == "interrupted"
    assert cm.current_context.buffered_response == "test response updated"
    
    # Test resume
    remaining = cm.resume_response()
    assert remaining == "test response updated"
    assert cm.current_context.current_state == ConversationState.SPEAKING
    
    # Test completion
    cm.complete_response()
    assert cm.current_context.current_state == ConversationState.IDLE
    assert cm.current_context.response_state.value == "completed"


def test_interrupt_without_threading():
    """Test interrupt functionality without complex threading"""
    cm = ConversationManager()
    cm.start_conversation()
    
    # Start response
    text = "This is a test response that should be interrupted"
    cm.start_response(text)
    cm.update_response(text[:20])  # Partial response
    
    # Interrupt
    cm.interrupt_response()
    
    # Verify state
    assert cm.current_context.current_state == ConversationState.INTERRUPTED
    assert cm.current_context.buffered_response == text[:20]
    
    # Resume
    remaining = cm.resume_response()
    assert remaining == text[:20]
    assert cm.current_context.current_state == ConversationState.SPEAKING
    
    # Complete
    cm.update_response(text)
    cm.complete_response()
    assert cm.current_context.current_state == ConversationState.IDLE


def test_simple_audio_interrupt():
    """Test audio interrupt with minimal complexity"""
    class SimpleAudioHandler:
        def __init__(self):
            self.interrupted = False
        
        def play_audio(self, duration):
            # Simulate audio playback
            time.sleep(duration)
            return not self.interrupted
        
        def interrupt(self):
            self.interrupted = True
    
    handler = SimpleAudioHandler()
    cm = ConversationManager()
    cm.start_conversation()
    
    # Start response
    cm.start_response("test")
    cm.update_state(ConversationState.SPEAKING)
    
    # Simulate audio playback with interrupt
    def play_and_interrupt():
        time.sleep(0.1)  # Let audio start
        handler.interrupt()
        cm.interrupt_response()
    
    # Start interrupt thread
    interrupt_thread = threading.Thread(target=play_and_interrupt)
    interrupt_thread.start()
    
    # Simulate audio playback
    audio_played = handler.play_audio(0.2)
    
    # Wait for interrupt
    interrupt_thread.join(timeout=1.0)
    
    # Verify interrupt occurred
    assert not audio_played  # Audio should have been interrupted
    assert cm.current_context.current_state == ConversationState.INTERRUPTED

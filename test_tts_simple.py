#!/usr/bin/env python3
"""Simple TTS test to verify Piper is working"""

import sys
import os
sys.path.insert(0, 'src')

from macbot.voice_assistant import tts_manager
import time

def test_tts():
    print("🧪 Testing TTS system...")
    
    # Initialize TTS
    tts_manager.init_engine()
    print(f"TTS Engine: {tts_manager.engine_type}")
    print(f"Engine loaded: {tts_manager.engine is not None}")
    
    if tts_manager.engine is None:
        print("❌ TTS engine not loaded")
        return False
    
    # Test simple synthesis
    text = "Hello, this is a test of the TTS system."
    print(f"Testing with: '{text}'")
    
    try:
        from piper import SynthesisConfig
        config = SynthesisConfig()
        config.length_scale = 1.0 / 1.2  # 1.2x speed
        config.noise_scale = 0.5
        config.noise_w = 0.6
        config.phoneme_silence_sec = 0.05
        
        print("Synthesizing audio...")
        start_time = time.time()
        audio_chunks = tts_manager.engine.synthesize(text, config)
        synthesis_time = time.time() - start_time
        
        print(f"✅ Synthesis completed in {synthesis_time:.2f}s")
        
        # Process generator
        audio_arrays = []
        for ch in audio_chunks:
            try:
                if hasattr(ch, 'audio_float_array'):
                    audio_arrays.append(ch.audio_float_array)
                elif hasattr(ch, 'audio'):
                    audio_arrays.append(np.array(ch.audio, dtype=np.float32))
                else:
                    audio_arrays.append(np.array(ch, dtype=np.float32))
            except Exception as e:
                print(f"Warning: Failed to process chunk: {e}")
                continue
        
        print(f"Generated {len(audio_arrays)} audio chunks")
        
        # Check if we got audio data
        if audio_arrays:
            print("✅ Audio generation successful")
            return True
        else:
            print("❌ No audio chunks generated")
            return False
            
    except Exception as e:
        print(f"❌ TTS synthesis failed: {e}")
        return False

if __name__ == "__main__":
    success = test_tts()
    sys.exit(0 if success else 1)

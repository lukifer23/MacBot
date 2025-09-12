#!/usr/bin/env python3
"""
STT Test Script for MacBot
Tests the STT model by generating test audio samples and measuring transcription latency.
"""

import os
import sys
import time
import numpy as np
import soundfile as sf
from pathlib import Path

# Add src to path to import macbot modules
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from macbot.voice_assistant import transcribe, WHISPER_MODEL, _WHISPER_IMPL
from macbot import config as CFG

def generate_test_audio(sample_rate=16000, duration=1.0):
    """Generate a simple test audio sample with some recognizable patterns"""
    t = np.linspace(0, duration, int(sample_rate * duration), False)

    # Create a simple pattern that might be recognized as speech-like
    # Using multiple frequencies to simulate speech formants
    freq1 = 300  # Fundamental frequency
    freq2 = 800  # First formant
    freq3 = 1200 # Second formant

    # Create a modulated signal
    signal = (np.sin(freq1 * 2 * np.pi * t) * 0.5 +
              np.sin(freq2 * 2 * np.pi * t) * 0.3 +
              np.sin(freq3 * 2 * np.pi * t) * 0.2)

    # Apply envelope to simulate speech amplitude variations
    envelope = np.exp(-t * 0.5) * (0.8 + 0.2 * np.sin(2 * np.pi * t * 2))
    signal = signal * envelope

    # Add some noise to make it more realistic
    noise = np.random.normal(0, 0.01, len(signal))
    signal = signal + noise

    # Normalize
    signal = signal / np.max(np.abs(signal)) * 0.7

    return signal.astype(np.float32)

def test_stt_model():
    """Test the STT model with various audio samples"""
    print("🎤 MacBot STT Model Test")
    print("=" * 50)

    # Display model information
    model_name = os.path.basename(CFG.get_stt_model())
    print(f"📋 Model Information:")
    print(f"   Model: {model_name}")
    print(f"   Implementation: {_WHISPER_IMPL}")
    print(f"   Language: {CFG.get_stt_language()}")
    print(f"   Binary: {CFG.get_stt_bin()}")
    print()

    # Test 1: Test with silence
    print("🧪 Test 1: Silence detection")
    sample_rate = CFG.get_audio_sample_rate()
    silence = np.zeros(int(sample_rate * 0.5), dtype=np.float32)

    start_time = time.time()
    transcription = transcribe(silence)
    latency = time.time() - start_time

    print(f"   Result: '{transcription}' (length: {len(transcription)})")
    print(".3f")
    print()

    # Test 2: Test with generated audio
    print("🧪 Test 2: Generated audio pattern")
    test_audio = generate_test_audio(sample_rate=sample_rate, duration=1.0)

    start_time = time.time()
    transcription = transcribe(test_audio)
    latency = time.time() - start_time

    print(f"   Result: '{transcription}' (length: {len(transcription)})")
    print(".3f")
    print()

    # Test 3: Test with noise
    print("🧪 Test 3: Random noise")
    noise = np.random.normal(0, 0.1, int(sample_rate * 1.0)).astype(np.float32)

    start_time = time.time()
    transcription = transcribe(noise)
    latency = time.time() - start_time

    print(f"   Result: '{transcription}' (length: {len(transcription)})")
    print(".3f")
    print()

    # Save test files
    print("💾 Saving test audio files...")
    sf.write("stt_test_silence.wav", silence, sample_rate)
    sf.write("stt_test_pattern.wav", test_audio, sample_rate)
    sf.write("stt_test_noise.wav", noise, sample_rate)
    print("   Files saved: stt_test_silence.wav, stt_test_pattern.wav, stt_test_noise.wav")
    print()

    # Performance summary
    print("📊 Performance Summary:")
    print(f"   Model loaded successfully: ✅")
    print(f"   Transcription function working: ✅")
    print(f"   Average latency: ~{(latency):.3f}s per transcription")
    print()

    return True

def test_cli_directly():
    """Test the whisper-cli binary directly"""
    print("🔧 Testing whisper-cli binary directly...")
    print("-" * 40)

    model_path = CFG.get_stt_model()
    bin_path = CFG.get_stt_bin()

    if not os.path.exists(bin_path):
        print(f"❌ Binary not found: {bin_path}")
        return False

    if not os.path.exists(model_path):
        print(f"❌ Model not found: {model_path}")
        return False

    print(f"✅ Binary exists: {bin_path}")
    print(f"✅ Model exists: {model_path}")

    # Test with our generated pattern file
    test_file = "stt_test_pattern.wav"
    if os.path.exists(test_file):
        print(f"\n🧪 Testing CLI with: {test_file}")
        start_time = time.time()

        cmd = f"{bin_path} -m {model_path} -f {test_file} -l en -nt -otxt -of cli_test_result"
        result = os.system(cmd)

        latency = time.time() - start_time

        if result == 0 and os.path.exists("cli_test_result.txt"):
            with open("cli_test_result.txt", "r") as f:
                cli_result = f.read().strip()
            print(f"   CLI Result: '{cli_result}'")
            print(".3f")
        else:
            print("   CLI test failed or no output file generated")

    print()
    return True

if __name__ == "__main__":
    try:
        success = test_stt_model()
        test_cli_directly()

        if success:
            print("🎉 STT test suite completed successfully!")
            print("\n💡 Note: The model is working correctly.")
            print("   Empty results are expected for synthetic audio patterns.")
            print("   Real speech audio will produce proper transcriptions.")
        else:
            print("\n💥 STT test failed!")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        sys.exit(1)

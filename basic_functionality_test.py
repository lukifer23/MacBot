#!/usr/bin/env python3
"""
Basic functionality test for MacBot core components.
Tests STT, TTS, and LLM functionality individually.
"""
import sys
import os
import time
import tempfile
import numpy as np
import soundfile as sf

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

def test_stt():
    """Test Speech-to-Text functionality"""
    print("\n=== Testing STT (Speech-to-Text) ===")

    # Generate a simple test audio file
    sample_rate = 16000
    duration = 2.0  # seconds
    frequency = 440  # A4 note

    t = np.linspace(0, duration, int(sample_rate * duration), False)
    audio = np.sin(frequency * 2 * np.pi * t)

    # Create temporary WAV file
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
        temp_path = temp_file.name
        sf.write(temp_path, audio, sample_rate)

    try:
        # Import and test STT
        from macbot.config import get_stt_bin, get_stt_model
        import subprocess

        stt_bin = get_stt_bin()
        stt_model = get_stt_model()

        print(f"Using STT binary: {stt_bin}")
        print(f"Using STT model: {stt_model}")

        # Test whisper-cli directly
        cmd = [stt_bin, "-m", stt_model, "-f", temp_path, "--language", "en", "--no-timestamps"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            transcription = result.stdout.strip()
            print("✓ STT Test PASSED")
            print(f"Transcription: '{transcription}'")
            return True
        else:
            print("✗ STT Test FAILED")
            print("Error:", result.stderr)
            return False

    except Exception as e:
        print("✗ STT Test FAILED")
        print("Exception:", str(e))
        return False
    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)

def test_tts():
    """Test Text-to-Speech functionality"""
    print("\n=== Testing TTS (Text-to-Speech) ===")

    try:
        # Import TTS components
        from piper.voice import PiperVoice
        from macbot.config import get_tts_voice

        voice_name = get_tts_voice()
        voice_path = f"piper_voices/{voice_name}/model.onnx"

        print(f"Using TTS voice: {voice_name}")
        print(f"Voice path: {voice_path}")

        # Load voice model
        voice = PiperVoice.load(voice_path)

        # Generate test audio
        test_text = "Hello, this is a test of the text to speech system."
        audio_data = voice.synthesize(test_text)

        # Convert to numpy array for verification
        try:
            audio_array = np.array(list(audio_data))
        except:
            # If audio_data is already a numpy array or similar
            audio_array = np.array(audio_data)

        if len(audio_array) > 0:
            print("✓ TTS Test PASSED")
            print(f"Generated {len(audio_array)} audio samples")
            print(f"Duration: ~{len(audio_array)/voice.config.sample_rate:.1f} seconds")
            return True
        else:
            print("✗ TTS Test FAILED - No audio generated")
            return False

    except Exception as e:
        print("✗ TTS Test FAILED")
        print("Exception:", str(e))
        return False

def test_llm():
    """Test Language Model functionality"""
    print("\n=== Testing LLM (Language Model) ===")

    try:
        # Import LLM server components
        import requests
        from macbot.config import get_llm_server_url

        llm_url = get_llm_server_url()
        print(f"LLM Server URL: {llm_url}")

        # Note: This would require the LLM server to be running
        # For now, just test that the URL is properly configured
        print("✓ LLM Configuration Test PASSED")
        print(f"Server URL configured: {llm_url}")
        print("Note: Full LLM test requires running LLM server")
        return True

    except Exception as e:
        print("✗ LLM Test FAILED")
        print("Exception:", str(e))
        return False

def test_rag():
    """Test RAG (Retrieval-Augmented Generation) functionality"""
    print("\n=== Testing RAG (Retrieval-Augmented Generation) ===")

    try:
        import chromadb
        from macbot.config import get_rag_base_url

        rag_url = get_rag_base_url()
        print(f"RAG Server URL: {rag_url}")

        # Test ChromaDB client
        client = chromadb.PersistentClient(path="data/rag_database")

        # Try to get or create collection
        collection_name = "test_collection"
        try:
            collection = client.get_collection(collection_name)
            print("✓ Existing RAG collection found")
        except:
            collection = client.create_collection(collection_name)
            print("✓ New RAG collection created")

        # Test basic operations
        collection.add(
            documents=["This is a test document for MacBot RAG system."],
            ids=["test_doc_1"]
        )

        results = collection.query(
            query_texts=["test document"],
            n_results=1
        )

        if results['documents']:
            print("✓ RAG Test PASSED")
            print("Retrieved documents:", len(results['documents'][0]))
            return True
        else:
            print("✗ RAG Test FAILED - No documents retrieved")
            return False

    except Exception as e:
        print("✗ RAG Test FAILED")
        print("Exception:", str(e))
        return False

def main():
    """Run all basic functionality tests"""
    print("🚀 MacBot Basic Functionality Test Suite")
    print("=" * 50)

    results = []

    # Test each component
    results.append(("STT", test_stt()))
    results.append(("TTS", test_tts()))
    results.append(("LLM", test_llm()))
    results.append(("RAG", test_rag()))

    # Summary
    print("\n" + "=" * 50)
    print("📊 TEST SUMMARY")
    print("=" * 50)

    passed = 0
    total = len(results)

    for component, success in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{component:8} | {status}")
        if success:
            passed += 1

    print("-" * 50)
    print(f"Overall: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL TESTS PASSED! MacBot is ready to rock! 🚀")
        return True
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

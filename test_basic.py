#!/usr/bin/env python3
"""
Basic test script to verify MacBot components are working
"""
import sys
import os
sys.path.insert(0, 'src')

def test_imports():
    """Test that all required modules can be imported"""
    print("🔍 Testing imports...")

    try:
        import flask
        print("✅ Flask available")
    except ImportError as e:
        print(f"❌ Flask import failed: {e}")
        return False

    try:
        import chromadb
        print("✅ ChromaDB available")
    except ImportError as e:
        print(f"❌ ChromaDB import failed: {e}")
        return False

    try:
        import whisper
        print("✅ OpenAI Whisper available")
    except ImportError as e:
        print(f"❌ OpenAI Whisper import failed: {e}")
        return False

    try:
        import sounddevice
        print("✅ SoundDevice available")
    except ImportError as e:
        print(f"❌ SoundDevice import failed: {e}")
        return False

    try:
        from macbot import config as CFG
        print("✅ MacBot config available")
    except ImportError as e:
        print(f"❌ MacBot config import failed: {e}")
        return False

    return True

def test_config():
    """Test configuration loading"""
    print("\n🔧 Testing configuration...")

    try:
        from macbot import config as CFG

        llm_url = CFG.get_llm_server_url()
        print(f"✅ LLM Server URL: {llm_url}")

        stt_model = CFG.get_stt_model()
        print(f"✅ STT Model: {stt_model}")

        return True
    except Exception as e:
        print(f"❌ Config test failed: {e}")
        return False

def test_whisper():
    """Test whisper basic functionality"""
    print("\n🎤 Testing Whisper...")

    try:
        import whisper
        import numpy as np

        # Test model loading (this might take time)
        print("Loading whisper model (this may take a moment)...")
        model = whisper.load_model("tiny")
        print("✅ Whisper model loaded successfully")

        # Test with dummy audio
        dummy_audio = np.random.random(16000)  # 1 second of random audio
        result = model.transcribe(dummy_audio)
        print("✅ Whisper transcription test passed")

        return True
    except Exception as e:
        print(f"❌ Whisper test failed: {e}")
        return False

def main():
    print("🧪 MacBot Basic Functionality Test")
    print("=" * 40)

    results = []

    # Test imports
    results.append(("Imports", test_imports()))

    # Test config
    results.append(("Configuration", test_config()))

    # Test whisper (optional, might be slow)
    try:
        results.append(("Whisper", test_whisper()))
    except Exception as e:
        print(f"⚠️  Whisper test skipped: {e}")
        results.append(("Whisper", "Skipped"))

    print("\n" + "=" * 40)
    print("📊 Test Results:")

    all_passed = True
    for test_name, result in results:
        status = "✅ PASS" if result == True else ("⚠️  SKIP" if result == "Skipped" else "❌ FAIL")
        print(f"  {test_name}: {status}")
        if result != True and result != "Skipped":
            all_passed = False

    if all_passed:
        print("\n🎉 All critical tests passed! MacBot should be ready.")
    else:
        print("\n⚠️  Some tests failed. Check the errors above.")

    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())

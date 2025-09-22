#!/usr/bin/env python3
"""
MacBot Audio Interruption System
Handles TTS interruption using macOS AudioQueue for real-time audio control
"""
import os
import sys
import time
import threading
import queue
import numpy as np
try:
    import sounddevice as sd  # PortAudio bindings
except Exception as _sd_e:  # Guard import failures; allow text-only fallback
    sd = None  # type: ignore
try:
    import soundfile as sf
except Exception:
    sf = None  # type: ignore
import typing
from typing import Optional, Any, Callable, List, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Type-only imports here if needed
from .logging_utils import setup_logger

logger = setup_logger("macbot.audio_interrupt", "logs/audio_interrupt.log")

class AudioInterruptHandler:
    """Handles audio playback interruption using macOS AudioQueue"""

    def __init__(self, sample_rate: int = 24000, output_device=None):
        self.sample_rate = sample_rate
        self.current_stream: Optional[typing.Any] = None  # type: ignore  # sd.OutputStream type not available at import time
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.interrupt_requested = False
        self.playback_thread: Optional[threading.Thread] = None
        self.interrupt_callbacks: List[Callable] = []
        self.output_device = output_device  # sd device index or name

        # Audio buffer management
        self.audio_buffer = np.array([], dtype=np.float32)
        self.buffer_lock = threading.Lock()

        # Voice activity detection for interruption
        self.vad_enabled = True
        self.vad_threshold = 0.01
        self.vad_buffer_size = int(sample_rate * 0.1)  # 100ms buffer
        self.vad_buffer = np.zeros(self.vad_buffer_size, dtype=np.float32)

    def start(self):
        """Start the audio interruption handler"""
        logger.info("Starting audio interruption handler")
        self.interrupt_requested = False

    def stop(self):
        """Stop the audio interruption handler"""
        logger.info("Stopping audio interruption handler")
        self.interrupt_requested = True
        self._stop_current_playback()

        if self.playback_thread and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=1.0)

    def play_audio(self, audio_data: np.ndarray, on_interrupt: Optional[Callable] = None) -> bool:
        """
        Play audio with interruption capability

        Args:
            audio_data: Audio data as numpy array
            on_interrupt: Callback function called when interrupted

        Returns:
            bool: True if playback completed, False if interrupted
        """
        # Test/CI friendly bypass: avoid CoreAudio access during tests or when sounddevice is missing
        try:
            if os.environ.get("MACBOT_NO_AUDIO", "0") == "1":
                logger.info("MACBOT_NO_AUDIO=1 set; skipping audio playback")
                return True
        except Exception:
            pass
        if sd is None:
            logger.warning("sounddevice not available; skipping audio playback")
            return True
        if self.interrupt_requested:
            logger.info("Playback skipped due to pending interrupt")
            # Reset the flag for future calls
            self.interrupt_requested = False
            return False

        # Reset interrupt flag for new playback
        self.interrupt_requested = False

        with self.buffer_lock:
            self.audio_buffer = audio_data.copy()
            self.is_playing = True

        if on_interrupt:
            self.interrupt_callbacks.append(on_interrupt)

        # Start playback in separate thread
        self.playback_thread = threading.Thread(
            target=self._playback_worker,
            daemon=True
        )
        self.playback_thread.start()

        # Wait for playback to complete or be interrupted with adaptive timeout
        try:
            # Derive an expected duration based on samples and sample_rate, add a small cushion
            expected_sec = max(3.0, min(120.0, (len(audio_data) / float(self.sample_rate)) + 1.0))
        except Exception:
            expected_sec = 15.0

        self.playback_thread.join(timeout=expected_sec)
        if self.playback_thread.is_alive():
            # Give one more grace period before forcing an interrupt
            logger.warning(
                f"Audio playback exceeded expected duration ({expected_sec:.1f}s); extending wait"
            )
            self.playback_thread.join(timeout=min(30.0, expected_sec))

        if self.playback_thread.is_alive():
            logger.warning("Audio playback still active; forcing interruption")
            self.interrupt_requested = True
            self._stop_current_playback()

        # Clean up callbacks
        if on_interrupt and on_interrupt in self.interrupt_callbacks:
            self.interrupt_callbacks.remove(on_interrupt)

        return not self.interrupt_requested

    def _playback_worker(self):
        """Worker thread for audio playback with interruption monitoring"""
        if sd is None:
            return
        try:
            # Create audio stream
            self.current_stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.float32,
                blocksize=int(self.sample_rate * 0.05),  # 50ms blocks
                callback=self._audio_callback,
                device=self.output_device
            )

            if self.current_stream:
                with self.current_stream:
                    while self.is_playing and not self.interrupt_requested:
                        time.sleep(0.01)  # Small sleep to prevent busy waiting
            else:
                while self.is_playing and not self.interrupt_requested:
                    time.sleep(0.01)  # Small sleep to prevent busy waiting

        except Exception as e:
            logger.error(f"Audio playback error: {e}")
        finally:
            self.is_playing = False
            self.current_stream = None

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """Audio callback for real-time interruption monitoring"""
        if self.interrupt_requested or not self.is_playing:
            outdata.fill(0)
            return

        try:
            # Get next chunk of audio data
            with self.buffer_lock:
                if len(self.audio_buffer) == 0:
                    self.is_playing = False
                    outdata.fill(0)
                    return

                # Extract chunk
                chunk_size = min(frames, len(self.audio_buffer))
                chunk = self.audio_buffer[:chunk_size]
                self.audio_buffer = self.audio_buffer[chunk_size:]

                # Zero-pad if necessary
                if len(chunk) < frames:
                    padded_chunk = np.zeros(frames, dtype=np.float32)
                    padded_chunk[:len(chunk)] = chunk
                    chunk = padded_chunk

            outdata[:] = chunk.reshape(-1, 1)

        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            outdata.fill(0)

    def interrupt_playback(self):
        """Interrupt current audio playback"""
        logger.info("Audio playback interruption requested")

        self.interrupt_requested = True
        self._stop_current_playback()

        # Call interrupt callbacks
        for callback in self.interrupt_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Interrupt callback error: {e}")

        # Clear callbacks after calling them
        self.interrupt_callbacks.clear()

    def _stop_current_playback(self):
        """Stop current audio playback"""
        self.is_playing = False

        if self.current_stream:
            try:
                self.current_stream.stop()
                self.current_stream.close()
            except Exception as e:
                logger.error(f"Error stopping audio stream: {e}")
            finally:
                self.current_stream = None

    def check_voice_activity(self, audio_chunk: np.ndarray) -> bool:
        """
        Check for voice activity in audio chunk (for interruption)

        Args:
            audio_chunk: Audio data chunk

        Returns:
            bool: True if voice activity detected
        """
        if not self.vad_enabled:
            return False

        # Simple energy-based VAD
        energy = np.sqrt(np.mean(audio_chunk ** 2))

        # Update VAD buffer
        self.vad_buffer = np.roll(self.vad_buffer, -len(audio_chunk))
        self.vad_buffer[-len(audio_chunk):] = audio_chunk

        # Calculate RMS of recent audio
        rms = np.sqrt(np.mean(self.vad_buffer ** 2))

        return rms > self.vad_threshold

    def set_vad_threshold(self, threshold: float):
        """Set voice activity detection threshold"""
        self.vad_threshold = threshold
        logger.info(f"VAD threshold set to {threshold}")

    def enable_vad(self, enabled: bool = True):
        """Enable or disable voice activity detection"""
        self.vad_enabled = enabled
        logger.info(f"VAD {'enabled' if enabled else 'disabled'}")

    def get_playback_status(self) -> dict:
        """Get current playback status"""
        return {
            'is_playing': self.is_playing,
            'interrupt_requested': self.interrupt_requested,
            'buffer_size': len(self.audio_buffer),
            'vad_enabled': self.vad_enabled,
            'vad_threshold': self.vad_threshold
        }


# Global audio interrupt handler instance
audio_handler = AudioInterruptHandler()

def get_audio_handler() -> AudioInterruptHandler:
    """Get the global audio interrupt handler instance"""
    return audio_handler

def interrupt_audio_playback():
    """Interrupt current audio playback (convenience function)"""
    audio_handler.interrupt_playback()

def play_audio_with_interrupt(audio_data: np.ndarray, on_interrupt: Optional[Callable] = None) -> bool:
    """
    Play audio with interruption capability (convenience function)

    Args:
        audio_data: Audio data as numpy array
        on_interrupt: Callback function called when interrupted

    Returns:
        bool: True if playback completed, False if interrupted
    """
    return audio_handler.play_audio(audio_data, on_interrupt)

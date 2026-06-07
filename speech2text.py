#!/usr/bin/env python3
"""
Speech-to-Text Global Hotkey Daemon
Records audio on Alt+Z and transcribes using OpenAI Whisper
"""
import os
import threading
import numpy as np
from pynput import keyboard
from pynput.keyboard import Key
import sounddevice as sd
import scipy.io.wavfile as wavfile
import whisper
import subprocess
import tempfile
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SpeechToTextDaemon:
    def __init__(self, hotkey_char='z'):
        self.is_recording = False
        self.audio_data = []
        self.sample_rate = 16000
        self.alt_pressed = False
        self.model = None
        self.recording_thread = None
        self.hotkey_char = hotkey_char
        self.load_model()

    def load_model(self):
        """Load Whisper model on first run"""
        logger.info("Loading Whisper model (this may take a moment)...")
        try:
            self.model = whisper.load_model("base")
            logger.info("✓ Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            sys.exit(1)

    def on_press(self, key):
        """Handle key press events"""
        try:
            if key == Key.alt:
                self.alt_pressed = True
            elif hasattr(key, 'char') and key.char == self.hotkey_char and self.alt_pressed:
                if not self.is_recording:
                    self.start_recording()
        except AttributeError:
            pass

    def on_release(self, key):
        """Handle key release events"""
        try:
            if key == Key.alt:
                self.alt_pressed = False
            elif hasattr(key, 'char') and key.char == self.hotkey_char:
                if self.is_recording:
                    self.stop_recording()
        except AttributeError:
            pass

    def start_recording(self):
        """Start recording audio"""
        self.is_recording = True
        self.audio_data = []
        logger.info("🎙️  Recording... (hold Alt+Z)")

        def record_audio():
            try:
                with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype='float32',
                    blocksize=4096
                ) as stream:
                    while self.is_recording:
                        data, _ = stream.read(4096)
                        self.audio_data.append(data)
            except Exception as e:
                logger.error(f"Recording error: {e}")
                self.is_recording = False

        self.recording_thread = threading.Thread(target=record_audio, daemon=True)
        self.recording_thread.start()

    def stop_recording(self):
        """Stop recording and transcribe"""
        if not self.is_recording:
            return

        self.is_recording = False
        logger.info("⏹️  Recording stopped, transcribing...")

        def transcribe_audio():
            try:
                if not self.audio_data:
                    logger.warning("No audio recorded")
                    return

                # Concatenate audio data
                audio_array = np.concatenate(self.audio_data)

                # Save to temporary file
                with tempfile.NamedTemporaryFile(
                    suffix='.wav',
                    delete=False
                ) as tmp_file:
                    wavfile.write(tmp_file.name, self.sample_rate, audio_array)
                    tmp_path = tmp_file.name

                # Transcribe with Whisper
                result = self.model.transcribe(tmp_path, language="en")
                text = result['text'].strip()

                # Output to both clipboard and stdout
                if text:
                    # Copy to clipboard
                    try:
                        process = subprocess.Popen(
                            ['xclip', '-selection', 'clipboard'],
                            stdin=subprocess.PIPE,
                            stderr=subprocess.DEVNULL
                        )
                        process.communicate(text.encode('utf-8'))
                        logger.info("✓ Copied to clipboard")
                    except FileNotFoundError:
                        logger.warning("xclip not found, skipping clipboard copy")

                    # Print to stdout (goes to active input)
                    print(text)
                    logger.info(f"✓ Transcribed: {text}")
                else:
                    logger.warning("No speech detected")

                # Cleanup
                os.unlink(tmp_path)

            except Exception as e:
                logger.error(f"Transcription error: {e}")

        transcribe_thread = threading.Thread(target=transcribe_audio, daemon=True)
        transcribe_thread.start()

    def run(self):
        """Start the daemon"""
        logger.info("=" * 60)
        logger.info("🎤 Speech-to-Text Daemon Started")
        logger.info(f"Press and hold Alt+{self.hotkey_char.upper()} to record")
        logger.info("=" * 60)

        try:
            with keyboard.Listener(
                on_press=self.on_press,
                on_release=self.on_release
            ) as listener:
                listener.join()
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
        except Exception as e:
            logger.error(f"Listener error: {e}")


if __name__ == '__main__':
    hotkey = os.environ.get('STT_HOTKEY', 'z').lower()
    daemon = SpeechToTextDaemon(hotkey_char=hotkey)
    daemon.run()

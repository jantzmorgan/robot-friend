import io
from collections import deque
import os
from pathlib import Path
import threading
import time
import wave

import numpy as np
import pyaudio
import openwakeword

from openwakeword.model import Model


# ============================================================
# SETTINGS
# ============================================================

RATE = 16000
CHANNELS = 1
CHUNK = 1280
DEFAULT_THRESHOLD = float(os.getenv("ROBOT_WAKE_THRESHOLD", "0.5"))
HERMAN_THRESHOLD = float(os.getenv("ROBOT_HERMAN_WAKE_THRESHOLD", "0.45"))
CUSTOM_MODEL = Path(__file__).resolve().parent / "models" / "hey_herman.onnx"


# ============================================================
# WAKE WORD LISTENER
# ============================================================

class WakeWordListener:

    def __init__(self, on_wake):

        self.on_wake = on_wake

        self.running = False

        self.thread = None

        self.audio = None
        self.stream = None
        self.model = None

        self.pause_requested = threading.Event()
        self.pause_complete = threading.Event()

        self.stream_lock = threading.Lock()
        self.pending_command_lock = threading.Lock()
        self.pending_command_audio = None
        self.handoff_capturing = threading.Event()


    # ========================================================
    # START
    # ========================================================

    def start(self):

        if self.running:
            return


        print("Loading OpenWakeWord...")


        openwakeword.utils.download_models()


        wake_models = ["hey_jarvis"]
        if CUSTOM_MODEL.exists():
            wake_models.insert(0, str(CUSTOM_MODEL))
        self.model = Model(wakeword_models=wake_models, inference_framework="onnx")


        self.audio = pyaudio.PyAudio()


        self.running = True


        self.thread = threading.Thread(
            target=self._listen_loop,
            daemon=True
        )


        self.thread.start()


        print("Wake word listener online.")

        print(
            'Wake phrases: "Hey Herman" (primary), "Hey Jarvis" (fallback)'
        )


    # ========================================================
    # OPEN MICROPHONE
    # ========================================================

    def _open_stream(self):

        with self.stream_lock:

            if self.stream is not None:
                return


            try:

                self.stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK
                )


                print(
                    "Wake word microphone active."
                )


            except Exception as error:

                self.stream = None

                print(
                    "Could not open wake word microphone:",
                    error
                )


    # ========================================================
    # CLOSE MICROPHONE
    # ========================================================

    def _close_stream(self):

        with self.stream_lock:

            if self.stream is None:
                return


            try:

                if self.stream.is_active():

                    self.stream.stop_stream()


            except Exception:
                pass


            try:

                self.stream.close()

            except Exception:
                pass


            self.stream = None


    # ========================================================
    # PAUSE
    #
    # Browser speech recognition needs the microphone during
    # an active conversation. Pausing OpenWakeWord prevents
    # two systems from fighting over the same Windows mic.
    # ========================================================

    def pause(self, wait=True):

        self.pause_requested.set()

        # The HTTP pause endpoint must not return until Windows has actually
        # released the input device for browser speech recognition.
        if wait and threading.current_thread() is not self.thread:
            timeout = 2.0
            if self.handoff_capturing.is_set():
                timeout = float(os.getenv("ROBOT_HANDOFF_MAX_SECONDS", "15")) + 2.0
            self.pause_complete.wait(timeout=timeout)


    # ========================================================
    # RESUME
    # ========================================================

    def resume(self):

        self.pause_complete.clear()
        self.pause_requested.clear()


    def take_pending_command(self):
        """Return and clear speech captured during the wake-to-browser handoff."""
        with self.pending_command_lock:
            audio_bytes = self.pending_command_audio
            self.pending_command_audio = None
            return audio_bytes


    def _wav_bytes(self, frames):
        if not frames:
            return None
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wav_file.setframerate(RATE)
            wav_file.writeframes(b"".join(frames))
        return output.getvalue()


    def _capture_handoff_command(self):
        """Capture speech immediately following the wake word on the open stream."""
        if self.stream is None:
            return None
        max_seconds = float(os.getenv("ROBOT_HANDOFF_MAX_SECONDS", "15"))
        silence_seconds = float(os.getenv("ROBOT_HANDOFF_SILENCE_SECONDS", "1.8"))
        start_timeout = float(os.getenv("ROBOT_HANDOFF_START_TIMEOUT_SECONDS", "4.0"))
        pre_roll = deque(maxlen=max(1, int(0.4 * RATE / CHUNK)))
        frames = []
        speech_started = False
        silent_chunks = 0
        silence_chunks_to_stop = max(1, int(silence_seconds * RATE / CHUNK))
        max_chunks = max(1, int(max_seconds * RATE / CHUNK))
        start_timeout_chunks = max(1, int(start_timeout * RATE / CHUNK))
        speech_threshold = 350

        print("Capturing immediate post-wake speech...")
        for chunk_index in range(max_chunks):
            raw_audio = self.stream.read(CHUNK, exception_on_overflow=False)
            level = float(np.sqrt(np.mean(
                np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) ** 2
            )))
            if level >= speech_threshold:
                if not speech_started:
                    frames.extend(pre_roll)
                speech_started = True
                silent_chunks = 0
            elif speech_started:
                silent_chunks += 1

            if speech_started:
                frames.append(raw_audio)
                if silent_chunks >= silence_chunks_to_stop:
                    break
            else:
                pre_roll.append(raw_audio)
                if chunk_index + 1 >= start_timeout_chunks:
                    break

        return self._wav_bytes(frames)


    # ========================================================
    # STATUS
    # ========================================================

    @property
    def paused(self):

        return self.pause_requested.is_set()


    # ========================================================
    # LISTEN LOOP
    # ========================================================

    def _listen_loop(self):

        last_activation = 0.0


        while self.running:


            # ------------------------------------------------
            # Conversation is active.
            #
            # Release the microphone completely so Chrome's
            # speech recognition has clean access to it.
            # ------------------------------------------------

            if self.pause_requested.is_set():

                self._close_stream()
                self.pause_complete.set()

                time.sleep(
                    0.05
                )

                continue


            # ------------------------------------------------
            # Make sure microphone is open.
            # ------------------------------------------------

            if self.stream is None:

                self._open_stream()


                if self.stream is None:

                    time.sleep(
                        1.0
                    )

                    continue


            try:

                raw_audio = self.stream.read(
                    CHUNK,
                    exception_on_overflow=False
                )


                audio_frame = np.frombuffer(
                    raw_audio,
                    dtype=np.int16
                )


                predictions = self.model.predict(
                    audio_frame
                )


                for model_name, score in predictions.items():

                    score = float(
                        score
                    )


                    threshold = HERMAN_THRESHOLD if "herman" in model_name.lower() else DEFAULT_THRESHOLD
                    if (
                        score >= threshold
                        and
                        time.time() -
                        last_activation >
                        2.0
                    ):

                        print(
                            f"Wake word detected: "
                            f"{model_name} "
                            f"score={score:.2f}"
                        )


                        last_activation = (
                            time.time()
                        )


                        self.model.reset()

                        with self.pending_command_lock:
                            self.pending_command_audio = None

                        self.on_wake()

                        # The browser cannot hear audio spoken while its WebRTC
                        # connection is starting. Preserve that first sentence
                        # on the already-open wake microphone for handoff.
                        self.handoff_capturing.set()
                        try:
                            handoff_audio = self._capture_handoff_command()
                            with self.pending_command_lock:
                                self.pending_command_audio = handoff_audio
                        finally:
                            self.handoff_capturing.clear()


                        break


            except Exception as error:

                print(
                    "Wake word error:",
                    error
                )


                self._close_stream()


                time.sleep(
                    0.25
                )


    # ========================================================
    # CAPTURE ONE POST-WAKE COMMAND
    # ========================================================

    def capture_command(self, max_seconds=15.0, silence_seconds=1.8):
        """Record one command, allowing natural pauses, and return WAV bytes."""
        if not self.pause_complete.wait(timeout=3.0):
            raise RuntimeError("Wake microphone did not release in time")

        command_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        frames = []
        # Keep a small rolling buffer so the consonant that crosses the voice
        # threshold is not the first sound written to the transcript WAV.
        pre_roll = deque(maxlen=max(1, int(0.4 * RATE / CHUNK)))
        speech_started = False
        silent_chunks = 0
        silence_chunks_to_stop = max(1, int(silence_seconds * RATE / CHUNK))
        max_chunks = max(1, int(max_seconds * RATE / CHUNK))
        speech_threshold = 350

        print("Listening for command...")
        try:
            for _ in range(max_chunks):
                raw_audio = command_stream.read(CHUNK, exception_on_overflow=False)
                level = float(np.sqrt(np.mean(
                    np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) ** 2
                )))

                if level >= speech_threshold:
                    if not speech_started:
                        frames.extend(pre_roll)
                    speech_started = True
                    silent_chunks = 0
                elif speech_started:
                    silent_chunks += 1

                if speech_started:
                    frames.append(raw_audio)
                    if silent_chunks >= silence_chunks_to_stop:
                        break
                else:
                    pre_roll.append(raw_audio)
        finally:
            try:
                command_stream.stop_stream()
            except Exception:
                pass
            command_stream.close()

        if not frames:
            print("No command speech detected.")
            return None

        return self._wav_bytes(frames)


    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        self.running = False


        self._close_stream()


        if self.audio is not None:

            try:

                self.audio.terminate()

            except Exception:
                pass


            self.audio = None

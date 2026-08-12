import io
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
THRESHOLD = 0.5

WAKE_MODEL_NAME = "hey_jarvis"


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


    # ========================================================
    # START
    # ========================================================

    def start(self):

        if self.running:
            return


        print("Loading OpenWakeWord...")


        openwakeword.utils.download_models()


        self.model = Model(
            wakeword_models=[
                WAKE_MODEL_NAME
            ]
        )


        self.audio = pyaudio.PyAudio()


        self.running = True


        self.thread = threading.Thread(
            target=self._listen_loop,
            daemon=True
        )


        self.thread.start()


        print("Wake word listener online.")

        print(
            'Temporary wake phrase: "Hey Jarvis"'
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
            self.pause_complete.wait(timeout=2.0)


    # ========================================================
    # RESUME
    # ========================================================

    def resume(self):

        self.pause_complete.clear()
        self.pause_requested.clear()


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


                    if (
                        score >= THRESHOLD
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


                        self.on_wake()


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
                    speech_started = True
                    silent_chunks = 0
                elif speech_started:
                    silent_chunks += 1

                if speech_started:
                    frames.append(raw_audio)
                    if silent_chunks >= silence_chunks_to_stop:
                        break
        finally:
            try:
                command_stream.stop_stream()
            except Exception:
                pass
            command_stream.close()

        if not frames:
            print("No command speech detected.")
            return None

        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wav_file.setframerate(RATE)
            wav_file.writeframes(b"".join(frames))
        return output.getvalue()


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
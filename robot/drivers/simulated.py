"""No-hardware drivers used on Windows and in automated tests."""

from __future__ import annotations

import io
import logging
import queue
import json
import os
import threading
import urllib.request
import wave

from robot.interfaces import (
    AudioInputDriver, AudioOutputDriver, CameraDriver, DisplayDriver,
    MotionDriver, SensorDriver, SensorSnapshot,
)

log = logging.getLogger(__name__)


class SimulatedMotion(MotionDriver):
    def __init__(self) -> None:
        self.linear = self.angular = 0.0

    def drive(self, linear: float, angular: float) -> None:
        self.linear, self.angular = linear, angular
        log.info("SIM motion linear=%.2f angular=%.2f", linear, angular)

    def stop(self) -> None:
        self.drive(0.0, 0.0)


class SimulatedSensors(SensorDriver):
    def __init__(self) -> None:
        self.snapshot = SensorSnapshot(distance_cm=100.0, battery_percent=100.0)

    def read(self) -> SensorSnapshot:
        return self.snapshot


class CameraAwareSensors(SensorDriver):
    """Adds camera telemetry without coupling the runtime to OpenCV."""

    def __init__(self, base: SensorDriver, camera) -> None:
        self.base, self.camera = base, camera

    def read(self) -> SensorSnapshot:
        base = self.base.read()
        camera = self.camera.status()
        return SensorSnapshot(
            distance_cm=base.distance_cm,
            battery_percent=base.battery_percent,
            bumper_pressed=base.bumper_pressed,
            camera_online=camera["camera_online"],
            person_visible=camera["person_visible"],
            details={
                **(base.details or {}),
                "camera_index": camera["camera_index"],
                "face_x": camera["face_x"],
                "face_y": camera["face_y"],
                "face_size": camera["face_size"],
            },
        )


class SimulatedCamera(CameraDriver):
    def capture(self):
        return None

    def close(self) -> None:
        pass


class SimulatedDisplay(DisplayDriver):
    def __init__(self) -> None:
        self.expression, self.message = "neutral", ""

    def show_face(self, expression: str, message: str = "") -> None:
        self.expression, self.message = expression, message
        log.info("SIM face expression=%s message=%s", expression, message)


class SimulatedAudioInput(AudioInputDriver):
    def __init__(self) -> None:
        self.messages: queue.Queue[str] = queue.Queue()

    def listen(self, timeout: float | None = None) -> str | None:
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None


class SimulatedAudioOutput(AudioOutputDriver):
    def __init__(self) -> None:
        self.last_spoken = ""

    def speak(self, text: str) -> None:
        self.last_spoken = text
        log.info("SIM speech: %s", text)

    def stop(self) -> None:
        pass


class KokoroAudioOutput(AudioOutputDriver):
    """Adapter for the existing local Kokoro/Fenrir HTTP service."""

    # The Kokoro service reports /speech/event at the first playable audio
    # chunk and again after playback. Do not mark the robot as speaking while
    # this adapter is merely waiting for speech generation.
    reports_playback = True

    def __init__(self, base_url: str = "http://127.0.0.1:8001") -> None:
        self.base_url = base_url.rstrip("/")

    def _post(self, path: str, payload: dict) -> None:
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=120):
            pass

    def speak(self, text: str) -> None:
        self._post("/speak", {"text": text})

    def stop(self) -> None:
        try:
            self._post("/stop", {})
        except Exception:
            log.exception("Could not stop Kokoro speech")


class OpenAIAudioOutput(AudioOutputDriver):
    """Generate a WAV reply with OpenAI and stream it to Windows speakers."""

    def __init__(self, api_key: str, *, model: str = "gpt-4o-mini-tts",
                 voice: str = "coral") -> None:
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self._stop_requested = threading.Event()
        self._on_playback_start = lambda: None
        self._on_playback_end = lambda: None

    def set_playback_callbacks(self, on_start, on_end) -> None:
        """Report actual speaker playback boundaries to the robot runtime."""
        self._on_playback_start = on_start
        self._on_playback_end = on_end

    def speak(self, text: str) -> None:
        if os.name != "nt":
            raise RuntimeError("OpenAI PC speech playback currently requires Windows")

        import pyaudio
        from openai import OpenAI

        self._stop_requested.clear()
        response = OpenAI(api_key=self.api_key).audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
            response_format="wav",
        )
        wav_bytes = response.content
        player = pyaudio.PyAudio()
        stream = None
        playback_started = False
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
                stream = player.open(
                    format=player.get_format_from_width(wav_file.getsampwidth()),
                    channels=wav_file.getnchannels(),
                    rate=wav_file.getframerate(),
                    output=True,
                )
                self._on_playback_start()
                playback_started = True
                while not self._stop_requested.is_set():
                    frames = wav_file.readframes(2048)
                    if not frames:
                        break
                    stream.write(frames)
        finally:
            if stream is not None:
                stream.stop_stream()
                stream.close()
            player.terminate()
            if playback_started:
                self._on_playback_end()

    def stop(self) -> None:
        self._stop_requested.set()

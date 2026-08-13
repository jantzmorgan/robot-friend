from __future__ import annotations

import threading
import time

from robot.interfaces import (
    AudioInputDriver, AudioOutputDriver, CameraDriver, DisplayDriver,
    MotionDriver, SensorDriver,
)
from robot.state import StateStore


class SafetyError(RuntimeError):
    pass


class RobotRuntime:
    def __init__(self, *, motion: MotionDriver, sensors: SensorDriver,
                 camera: CameraDriver, display: DisplayDriver,
                 audio_input: AudioInputDriver, audio_output: AudioOutputDriver,
                 obstacle_stop_cm: float = 20.0) -> None:
        self.motion, self.sensors, self.camera = motion, sensors, camera
        self.display = display
        self.audio_input, self.audio_output = audio_input, audio_output
        self.obstacle_stop_cm = obstacle_stop_cm
        self.state = StateStore()
        self._audio_reports_playback = bool(
            getattr(audio_output, "reports_playback", False)
            or hasattr(audio_output, "set_playback_callbacks")
        )
        if hasattr(audio_output, "set_playback_callbacks"):
            audio_output.set_playback_callbacks(
                lambda: self.state.update(speaking=True, mode="speaking"),
                lambda: self.state.update(speaking=False, mode="idle"),
            )
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="robot-loop")
        self._thread.start()

    def _loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                reading = self.sensors.read()
                data = {
                    "distance_cm": reading.distance_cm,
                    "battery_percent": reading.battery_percent,
                    "bumper_pressed": reading.bumper_pressed,
                    "camera_online": reading.camera_online,
                    "person_visible": reading.person_visible,
                    **(reading.details or {}),
                }
                self.state.update(sensors=data, last_error=None)
                if reading.bumper_pressed or (
                    reading.distance_cm is not None
                    and reading.distance_cm < self.obstacle_stop_cm
                    and self.state.snapshot()["moving"]
                ):
                    self.emergency_stop("Obstacle or bumper safety trigger")
            except Exception as error:
                self.emergency_stop(f"Sensor failure: {error}")
            self._shutdown.wait(0.1)

    def drive(self, linear: float, angular: float, duration: float | None = None) -> None:
        if not -1.0 <= linear <= 1.0 or not -1.0 <= angular <= 1.0:
            raise ValueError("linear and angular must be between -1.0 and 1.0")
        if self.state.snapshot()["emergency_stopped"]:
            raise SafetyError("Emergency stop is latched; call /safety/reset first")
        reading = self.sensors.read()
        if linear > 0 and reading.distance_cm is not None and reading.distance_cm < self.obstacle_stop_cm:
            self.emergency_stop("Forward motion blocked by obstacle")
            raise SafetyError("Obstacle too close for forward motion")
        self.motion.drive(linear, angular)
        self.state.update(moving=bool(linear or angular), linear_speed=linear, angular_speed=angular, mode="moving")
        if duration is not None:
            threading.Thread(target=self._timed_stop, args=(min(duration, 10.0),), daemon=True).start()

    def _timed_stop(self, duration: float) -> None:
        time.sleep(max(0.0, duration))
        self.stop()

    def stop(self) -> None:
        self.motion.stop()
        self.state.update(moving=False, linear_speed=0.0, angular_speed=0.0, mode="idle")

    def emergency_stop(self, reason: str = "Emergency stop requested") -> None:
        self.motion.stop()
        self.audio_output.stop()
        self.state.update(moving=False, speaking=False, linear_speed=0.0,
                          angular_speed=0.0, emergency_stopped=True,
                          mode="stopped", last_error=reason)
        self.display.show_face("alert", reason)

    def reset_safety(self) -> None:
        reading = self.sensors.read()
        if reading.bumper_pressed:
            raise SafetyError("Cannot reset while bumper is pressed")
        self.state.update(emergency_stopped=False, last_error=None, mode="idle")
        self.display.show_face("neutral")

    def set_face(self, expression: str, message: str = "") -> None:
        self.display.show_face(expression, message)
        self.state.update(expression=expression)

    def speak(self, text: str) -> None:
        # Drivers with playback callbacks keep the mouth still while speech is
        # being synthesized, then animate it only while sound reaches speakers.
        if not self._audio_reports_playback:
            self.state.update(speaking=True, mode="speaking")
        try:
            self.audio_output.speak(text)
        finally:
            self.state.update(speaking=False, mode="idle")

    def close(self) -> None:
        self._shutdown.set()
        self.emergency_stop("Runtime shutdown")
        self.camera.close()

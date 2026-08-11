"""Contracts implemented by simulated and physical hardware drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SensorSnapshot:
    distance_cm: float | None = None
    battery_percent: float | None = None
    bumper_pressed: bool = False
    camera_online: bool = False
    person_visible: bool = False
    details: dict[str, Any] | None = None


class MotionDriver(ABC):
    @abstractmethod
    def drive(self, linear: float, angular: float) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...


class SensorDriver(ABC):
    @abstractmethod
    def read(self) -> SensorSnapshot: ...


class CameraDriver(ABC):
    @abstractmethod
    def capture(self) -> Any | None: ...

    @abstractmethod
    def close(self) -> None: ...


class DisplayDriver(ABC):
    @abstractmethod
    def show_face(self, expression: str, message: str = "") -> None: ...


class AudioInputDriver(ABC):
    @abstractmethod
    def listen(self, timeout: float | None = None) -> str | None: ...


class AudioOutputDriver(ABC):
    @abstractmethod
    def speak(self, text: str) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

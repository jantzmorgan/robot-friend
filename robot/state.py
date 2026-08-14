from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RobotState:
    mode: str = "idle"
    expression: str = "neutral"
    face_theme: str = "rainbow"
    face_colors: list[str] = field(
        default_factory=lambda: ["#42E8FF", "#7B8CFF", "#FF4FC8"]
    )
    face_effect: str = "auto"
    speaking: bool = False
    listening: bool = False
    wake_detected: bool = False
    wake_counter: int = 0
    wake_word_online: bool = False
    wake_paused: bool = False
    moving: bool = False
    emergency_stopped: bool = False
    linear_speed: float = 0.0
    angular_speed: float = 0.0
    sensors: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StateStore:
    def __init__(self) -> None:
        self._state = RobotState()
        self._lock = threading.RLock()

    def update(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            for key, value in changes.items():
                if not hasattr(self._state, key):
                    raise KeyError(f"Unknown robot state field: {key}")
                setattr(self._state, key, value)
            self._state.updated_at = datetime.now(timezone.utc).isoformat()
            return asdict(self._state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._state)

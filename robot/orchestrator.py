"""Optional autonomous behavior loop; API/manual control works without it."""

import logging
import threading

from robot.runtime import RobotRuntime

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, runtime: RobotRuntime) -> None:
        self.runtime = runtime
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.runtime.start()
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="orchestrator")
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            state = self.runtime.state.snapshot()
            if state["emergency_stopped"]:
                continue
            # Phase 1 autonomous behaviors (face tracking, roaming, docking)
            # plug in here. Manual/API control remains the safe default.

    def stop(self) -> None:
        self._stop.set()
        self.runtime.close()

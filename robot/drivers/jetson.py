"""Physical-driver integration points for Phase 1.

Install the Waveshare vendor library on the Jetson, then implement the small
methods below without changing the runtime or API. Imports intentionally occur
inside constructors so this module remains importable on Windows.
"""

from robot.interfaces import MotionDriver, SensorDriver, SensorSnapshot


class WaveshareMotion(MotionDriver):
    def __init__(self) -> None:
        raise RuntimeError(
            "WaveshareMotion is a driver stub. Add the exact UGV Rover vendor "
            "SDK calls after the Phase 1 controller/firmware is confirmed."
        )

    def drive(self, linear: float, angular: float) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class WaveshareSensors(SensorDriver):
    def __init__(self) -> None:
        raise RuntimeError("WaveshareSensors awaits the exact Rover sensor SDK.")

    def read(self) -> SensorSnapshot:
        raise NotImplementedError

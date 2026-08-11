import os

from robot.drivers import (
    SimulatedAudioInput, SimulatedAudioOutput, SimulatedCamera,
    SimulatedDisplay, SimulatedMotion, SimulatedSensors,
    KokoroAudioOutput,
    CameraAwareSensors, OpenCVCamera,
)
from robot.runtime import RobotRuntime


def create_runtime(mode: str | None = None) -> RobotRuntime:
    selected = (mode or os.getenv("ROBOT_HARDWARE", "sim")).lower()
    if selected != "sim":
        raise RuntimeError(
            f"Hardware mode {selected!r} is not configured. Use ROBOT_HARDWARE=sim "
            "until the Waveshare driver is wired in robot/drivers/jetson.py."
        )
    audio_output = (
        KokoroAudioOutput(os.getenv("ROBOT_TTS_URL", "http://127.0.0.1:8001"))
        if os.getenv("ROBOT_AUDIO", "sim").lower() == "kokoro"
        else SimulatedAudioOutput()
    )
    camera = SimulatedCamera()
    sensors = SimulatedSensors()
    camera_mode = "sim" if mode is not None else os.getenv("ROBOT_CAMERA", "opencv").lower()
    if camera_mode == "opencv":
        camera = OpenCVCamera(max_index=int(os.getenv("ROBOT_CAMERA_MAX_INDEX", "3")))
        sensors = CameraAwareSensors(sensors, camera)
    elif camera_mode != "sim":
        raise RuntimeError(f"Unsupported ROBOT_CAMERA mode: {camera_mode!r}")
    return RobotRuntime(
        motion=SimulatedMotion(), sensors=sensors,
        camera=camera, display=SimulatedDisplay(),
        audio_input=SimulatedAudioInput(), audio_output=audio_output,
    )

import os

from robot.drivers import (
    SimulatedAudioInput, SimulatedAudioOutput, SimulatedCamera,
    SimulatedDisplay, SimulatedMotion, SimulatedSensors,
    KokoroAudioOutput, OpenAIAudioOutput,
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
    audio_mode = "silent" if mode is not None else os.getenv("ROBOT_AUDIO", "auto").lower()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if audio_mode == "kokoro":
        audio_output = KokoroAudioOutput(os.getenv("ROBOT_TTS_URL", "http://127.0.0.1:8001"))
    elif audio_mode in {"auto", "openai", "sim"} and api_key:
        # Older Windows setups used ROBOT_AUDIO=sim. With an API key present,
        # upgrade that legacy value to real speech so startup works immediately.
        audio_output = OpenAIAudioOutput(
            api_key,
            model=os.getenv("ROBOT_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.getenv("ROBOT_TTS_VOICE", "coral"),
        )
    elif audio_mode in {"auto", "sim", "silent"}:
        audio_output = SimulatedAudioOutput()
    else:
        raise RuntimeError(f"Unsupported ROBOT_AUDIO mode: {audio_mode!r}")
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

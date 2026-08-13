import unittest

from robot.factory import create_runtime
from robot.interfaces import AudioOutputDriver, SensorSnapshot
from robot.runtime import RobotRuntime, SafetyError


class SelfReportingAudio(AudioOutputDriver):
    reports_playback = True

    def speak(self, text):
        self.text = text

    def stop(self):
        pass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.robot = create_runtime("sim")

    def tearDown(self):
        self.robot.close()

    def test_drive_and_stop(self):
        self.robot.drive(0.5, -0.2)
        self.assertTrue(self.robot.state.snapshot()["moving"])
        self.robot.stop()
        self.assertFalse(self.robot.state.snapshot()["moving"])

    def test_emergency_stop_latches(self):
        self.robot.emergency_stop("test")
        with self.assertRaises(SafetyError):
            self.robot.drive(0.5, 0.0)
        self.robot.reset_safety()
        self.robot.drive(0.5, 0.0)

    def test_obstacle_blocks_forward_motion(self):
        self.robot.sensors.snapshot = SensorSnapshot(distance_cm=10.0)
        with self.assertRaises(SafetyError):
            self.robot.drive(0.5, 0.0)

    def test_speech_state_waits_for_physical_playback_event(self):
        self.robot.speak("hello")
        self.assertFalse(self.robot.state.snapshot()["speaking"])

    def test_self_reporting_audio_does_not_start_mouth_during_generation(self):
        robot = RobotRuntime(
            motion=self.robot.motion,
            sensors=self.robot.sensors,
            camera=self.robot.camera,
            display=self.robot.display,
            audio_input=self.robot.audio_input,
            audio_output=SelfReportingAudio(),
        )
        robot.speak("hello")
        self.assertFalse(robot.state.snapshot()["speaking"])


if __name__ == "__main__":
    unittest.main()

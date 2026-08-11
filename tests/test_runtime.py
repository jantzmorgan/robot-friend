import unittest

from robot.factory import create_runtime
from robot.interfaces import SensorSnapshot
from robot.runtime import SafetyError


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


if __name__ == "__main__":
    unittest.main()

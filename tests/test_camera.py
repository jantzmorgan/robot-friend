import unittest

from robot.drivers.simulated import CameraAwareSensors, SimulatedSensors


class FakeCamera:
    def status(self):
        return {"camera_online": True, "camera_index": 2, "person_visible": True,
                "face_x": 0.25, "face_y": -0.5, "face_size": 0.1}


class CameraTelemetryTests(unittest.TestCase):
    def test_camera_status_is_merged_into_sensor_snapshot(self):
        snapshot = CameraAwareSensors(SimulatedSensors(), FakeCamera()).read()
        self.assertTrue(snapshot.camera_online)
        self.assertTrue(snapshot.person_visible)
        self.assertEqual(snapshot.details["camera_index"], 2)
        self.assertEqual(snapshot.details["face_x"], 0.25)


if __name__ == "__main__":
    unittest.main()

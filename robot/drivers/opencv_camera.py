"""USB/webcam driver used for Windows development and USB cameras on Jetson."""

from __future__ import annotations

import logging
import os
import threading
import time

from robot.interfaces import CameraDriver

log = logging.getLogger(__name__)


class OpenCVCamera(CameraDriver):
    def __init__(self, max_index: int = 3) -> None:
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("OpenCV is not installed; run: pip install opencv-python") from error

        self.cv2 = cv2
        self.max_index = max_index
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._frame = None
        self._camera = None
        self._status = {
            "camera_online": False,
            "camera_index": None,
            "person_visible": False,
            "face_x": 0.0,
            "face_y": 0.0,
            "face_size": 0.0,
        }
        self._detector = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self._thread = threading.Thread(target=self._run, daemon=True, name="opencv-camera")
        self._thread.start()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def capture(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def _open_camera(self):
        backend = self.cv2.CAP_DSHOW if os.name == "nt" else self.cv2.CAP_ANY
        for index in range(self.max_index + 1):
            camera = self.cv2.VideoCapture(index, backend)
            if not camera.isOpened():
                camera.release()
                continue
            camera.set(self.cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(self.cv2.CAP_PROP_FRAME_HEIGHT, 480)
            camera.set(self.cv2.CAP_PROP_FPS, 30)
            for _ in range(5):
                success, frame = camera.read()
                if success and frame is not None:
                    with self._lock:
                        self._camera = camera
                        self._frame = frame
                        self._status.update(camera_online=True, camera_index=index)
                    log.info("Camera %s online", index)
                    return camera
                time.sleep(0.1)
            camera.release()
        return None

    def _set_offline(self) -> None:
        with self._lock:
            self._camera = None
            self._frame = None
            self._status.update(
                camera_online=False, camera_index=None, person_visible=False,
                face_x=0.0, face_y=0.0, face_size=0.0,
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            camera = self._open_camera()
            if camera is None:
                self._set_offline()
                self._stop.wait(2.0)
                continue

            failed_frames = 0
            while not self._stop.is_set():
                success, frame = camera.read()
                if not success or frame is None:
                    failed_frames += 1
                    if failed_frames >= 10:
                        break
                    self._stop.wait(0.1)
                    continue

                failed_frames = 0
                height, width = frame.shape[:2]
                gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
                faces = self._detector.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=5, minSize=(70, 70)
                )
                status = {"person_visible": False, "face_x": 0.0,
                          "face_y": 0.0, "face_size": 0.0}
                if len(faces):
                    x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
                    status = {
                        "person_visible": True,
                        "face_x": ((x + w / 2) / width) * 2 - 1,
                        "face_y": ((y + h / 2) / height) * 2 - 1,
                        "face_size": (w * h) / float(width * height),
                    }
                with self._lock:
                    self._frame = frame
                    self._status.update(camera_online=True, **status)
                self._stop.wait(0.04)

            camera.release()
            self._set_offline()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            camera = self._camera
        if camera is not None:
            camera.release()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

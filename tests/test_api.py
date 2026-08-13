import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ["ROBOT_HARDWARE"] = "sim"
os.environ["ROBOT_CAMERA"] = "sim"
os.environ["ROBOT_MEMORY_PATH"] = os.path.join(tempfile.gettempdir(), "robot_friend_test.db")

from brain.server import app, apply_spoken_face_colors, realtime_session_config, runtime


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health_and_motion(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        response = self.client.post("/motion", json={"linear": 0.2, "angular": 0})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["moving"])
        self.client.post("/motion/stop")

    def test_safety_latch(self):
        self.client.post("/safety/stop", json={"reason": "test"})
        self.assertEqual(self.client.post("/motion", json={"linear": 0.2}).status_code, 409)
        self.assertEqual(self.client.post("/safety/reset").status_code, 200)

    def test_wake_state_contract_for_face(self):
        state = self.client.get("/state").get_json()
        self.assertIn("wake_counter", state)
        self.assertIn("wake_paused", state)
        self.assertIn("wake_word_online", state)

    def test_spoken_face_color_commands(self):
        colors = apply_spoken_face_colors("Make your face blue and pink")
        self.assertEqual(colors, ["#55CFFF", "#FF4FC8"])
        self.assertEqual(runtime.state.snapshot()["face_colors"], colors)

    def test_non_command_does_not_change_face(self):
        self.assertIsNone(apply_spoken_face_colors("What color is the sky?"))

    def test_rainbow_and_emotional_effect_commands(self):
        apply_spoken_face_colors("Go full rainbow and catch fire")
        state = runtime.state.snapshot()
        self.assertEqual(state["face_theme"], "rainbow")
        self.assertEqual(state["face_effect"], "fire")

        apply_spoken_face_colors("Stop effects")
        self.assertEqual(runtime.state.snapshot()["face_effect"], "none")

    def test_tears_can_start_and_stop(self):
        apply_spoken_face_colors("Cry for me")
        self.assertEqual(runtime.state.snapshot()["face_effect"], "tears")

        apply_spoken_face_colors("Stop crying")
        state = runtime.state.snapshot()
        self.assertEqual(state["face_effect"], "none")
        self.assertEqual(state["expression"], "normal")

    def test_fire_can_start_and_stop(self):
        apply_spoken_face_colors("Start the fire")
        self.assertEqual(runtime.state.snapshot()["face_effect"], "fire")

        apply_spoken_face_colors("Turn off the fire")
        self.assertEqual(runtime.state.snapshot()["face_effect"], "none")

    def test_mad_face_replaces_tears_with_automatic_fire(self):
        apply_spoken_face_colors("Cry")
        apply_spoken_face_colors("Switch to a mad face")
        state = runtime.state.snapshot()
        self.assertEqual(state["expression"], "annoyed")
        self.assertEqual(state["face_effect"], "auto")

    def test_realtime_config_uses_semantic_vad_and_short_audio_output(self):
        config = realtime_session_config()
        self.assertEqual(config["output_modalities"], ["audio"])
        turn = config["audio"]["input"]["turn_detection"]
        self.assertEqual(turn["type"], "semantic_vad")
        self.assertTrue(turn["create_response"])
        self.assertTrue(turn["interrupt_response"])
        self.assertIn("under 20 words", config["instructions"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "ROBOT_REALTIME": "1"})
    @patch("brain.server.httpx.post")
    def test_realtime_sdp_is_proxied_without_exposing_key(self, post):
        upstream = Mock(status_code=200, text="answer-sdp", is_error=False)
        post.return_value = upstream
        response = self.client.post(
            "/realtime/session", data="offer-sdp", content_type="application/sdp"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "answer-sdp")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertIn("semantic_vad", kwargs["files"]["session"][1])

    def test_realtime_state_and_turn_endpoints(self):
        response = self.client.post("/realtime/event", json={"state": "thinking"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(runtime.state.snapshot()["mode"], "thinking")
        response = self.client.post(
            "/realtime/turn", json={"user": "Hello", "assistant": "Hey there."}
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/realtime/end")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(runtime.state.snapshot()["wake_detected"])


if __name__ == "__main__":
    unittest.main()

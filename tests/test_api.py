import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ["ROBOT_HARDWARE"] = "sim"
os.environ["ROBOT_CAMERA"] = "sim"
os.environ["ROBOT_MEMORY_PATH"] = os.path.join(tempfile.gettempdir(), "robot_friend_test.db")

from brain.server import (
    app, apply_spoken_face_colors, arm_realtime_guard,
    realtime_readiness, realtime_session_config, robot_context, runtime,
)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health_and_motion(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn("realtime_ready", health.get_json()["services"])
        face_response = self.client.get("/")
        self.assertIn("no-store", face_response.headers["Cache-Control"])
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

    def test_start_crying_naturally_means_blue_tears(self):
        apply_spoken_face_colors("Jarvis, start crying")
        state = runtime.state.snapshot()
        self.assertEqual(state["face_effect"], "tears")
        self.assertEqual(state["expression"], "sad")

    def test_dance_mode_can_start_and_stop(self):
        apply_spoken_face_colors("Go into dance mode")
        state = runtime.state.snapshot()
        self.assertEqual(state["face_effect"], "dance")
        self.assertEqual(state["expression"], "excited")

        apply_spoken_face_colors("Stop dancing")
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
        self.assertIn("2-6 concise, complete sentences", config["instructions"])
        self.assertGreaterEqual(config["max_output_tokens"], 800)

    def test_robot_knows_spoken_face_commands_are_real(self):
        context = robot_context("Make your face blue")
        self.assertIn("local appearance controller", context.lower())
        self.assertIn("treat the change as successfully performed", context)
        self.assertIn("Never say you cannot change your face color", context)
        self.assertIn("real dance, party, and disco", context)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "", "ROBOT_REALTIME": "1"})
    def test_missing_key_is_reported_before_session_handoff(self):
        ready, reason = realtime_readiness()
        self.assertFalse(ready)
        self.assertIn("OPENAI_API_KEY", reason)
        response = self.client.post(
            "/realtime/session", data="v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
            content_type="application/sdp"
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENAI_API_KEY", response.get_json()["error"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "ROBOT_REALTIME": "1"})
    @patch("brain.server.httpx.post")
    def test_realtime_sdp_is_proxied_without_exposing_key(self, post):
        upstream = Mock(status_code=200, text="answer-sdp", is_error=False)
        post.return_value = upstream
        response = self.client.post(
            "/realtime/session", data="v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
            content_type="application/sdp"
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

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "ROBOT_REALTIME": "1"})
    @patch("brain.server.httpx.post")
    def test_realtime_token_uses_ephemeral_client_secret_flow(self, post):
        upstream = Mock(status_code=200, is_error=False)
        upstream.json.return_value = {"value": "ek_test"}
        post.return_value = upstream
        response = self.client.post("/realtime/token", json={"client_id": "face-test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["value"], "ek_test")
        self.assertEqual(response.get_json()["robot_client_id"], "face-test")
        self.assertIn("/v1/realtime/client_secrets", post.call_args.args[0])
        self.assertIn("session", post.call_args.kwargs["json"])

    def test_realtime_watchdog_recovers_abandoned_browser_session(self):
        runtime.state.update(
            listening=False, wake_detected=True, wake_paused=True, mode="thinking"
        )
        arm_realtime_guard(0.01)
        time.sleep(0.04)
        state = runtime.state.snapshot()
        self.assertTrue(state["listening"])
        self.assertFalse(state["wake_paused"])
        self.assertFalse(state["wake_detected"])
        self.assertEqual(state["mode"], "idle")

    def test_realtime_heartbeat_is_available(self):
        self.assertEqual(self.client.post("/realtime/heartbeat").status_code, 200)


if __name__ == "__main__":
    unittest.main()

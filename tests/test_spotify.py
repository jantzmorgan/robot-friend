import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from integrations.spotify import SpotifyController, SpotifyError


class SpotifyControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = SpotifyController(
            "public-client-id", "http://127.0.0.1:8000/spotify/callback",
            Path(tempfile.gettempdir()) / "herman_spotify_test_token.json",
        )
        self.controller.disconnect()

    def tearDown(self):
        self.controller.disconnect()

    def test_pkce_authorization_uses_no_client_secret(self):
        query = parse_qs(urlparse(self.controller.authorization_url()).query)
        self.assertEqual(query["client_id"], ["public-client-id"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertIn("code_challenge", query)
        self.assertNotIn("client_secret", query)

    def test_commands_require_a_connected_account(self):
        with self.assertRaisesRegex(SpotifyError, "not connected"):
            self.controller.command("play Around the World")


if __name__ == "__main__":
    unittest.main()

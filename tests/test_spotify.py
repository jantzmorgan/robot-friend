import tempfile
import unittest
from unittest.mock import patch
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

    def test_playlist_artist_random_liked_and_queue_commands(self):
        self.controller.token_path.write_text(
            '{"access_token":"test","refresh_token":"refresh","expires_at":99999999999}',
            encoding="utf-8",
        )
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            class Response:
                status_code = 200
                def json(self):
                    if path == "/me/player/devices":
                        return {"devices": [{"id": "laptop", "is_active": True}]}
                    if path == "/me/playlists":
                        return {"items": [{"name": "Road Trip", "uri": "spotify:playlist:road"}], "next": None}
                    if path == "/me/tracks":
                        return {"items": [{"track": {"uri": "spotify:track:liked"}}]}
                    if path == "/search" and kwargs["params"]["type"] == "artist":
                        return {"artists": {"items": [{"name": "Daft Punk", "uri": "spotify:artist:daft"}]}}
                    return {"tracks": {"items": [{"name": "One More Time", "uri": "spotify:track:one", "artists": [{"name": "Daft Punk"}]}]}}
            return Response()

        with patch.object(self.controller, "_request", side_effect=fake_request), \
             patch.object(self.controller, "_start_later") as start:
            self.assertIn("Road Trip", self.controller.command("shuffle my Road Trip playlist")["message"])
            start.assert_called_with({"context_uri": "spotify:playlist:road"}, shuffle=True)
            self.assertIn("Daft Punk", self.controller.command("play songs by Daft Punk")["message"])
            self.assertIn("One More Time", self.controller.command("play a random song by Daft Punk")["message"])
            self.assertIn("Liked Songs", self.controller.command("shuffle my liked songs")["message"])
            self.assertEqual(self.controller.command("add One More Time to the queue")["action"], "queue")

    def test_exact_song_request_selects_matching_artist_not_first_result(self):
        self.controller.token_path.write_text(
            '{"access_token":"test","refresh_token":"refresh","expires_at":99999999999}',
            encoding="utf-8",
        )

        def fake_request(method, path, **kwargs):
            class Response:
                status_code = 200
                def json(self):
                    if path == "/me/player/devices":
                        return {"devices": [{"id": "laptop", "is_active": True}]}
                    return {"tracks": {"items": [
                        {"name": "Hurt", "uri": "spotify:track:wrong", "popularity": 99,
                         "artists": [{"name": "Nine Inch Nails"}]},
                        {"name": "Hurt", "uri": "spotify:track:right", "popularity": 80,
                         "artists": [{"name": "Johnny Cash"}]},
                    ]}}
            return Response()

        with patch.object(self.controller, "_request", side_effect=fake_request) as request, \
             patch.object(self.controller, "_start_later") as start:
            result = self.controller.command("play Hurt by Johnny Cash")
            self.assertEqual(result["message"], "Playing Hurt by Johnny Cash.")
            start.assert_called_once_with({"uris": ["spotify:track:right"]})
            search = next(call for call in request.call_args_list if call.args[1] == "/search")
            self.assertEqual(search.kwargs["params"]["limit"], 10)
            self.assertIn('track:"Hurt"', search.kwargs["params"]["q"])
            self.assertIn('artist:"Johnny Cash"', search.kwargs["params"]["q"])

    def test_exact_song_request_avoids_unrequested_live_version(self):
        tracks = [
            {"name": "Dreams - Live", "uri": "spotify:track:live", "popularity": 90,
             "artists": [{"name": "Fleetwood Mac"}]},
            {"name": "Dreams", "uri": "spotify:track:studio", "popularity": 70,
             "artists": [{"name": "Fleetwood Mac"}]},
        ]
        selected = self.controller._best_track_match(tracks, "Dreams", "Fleetwood Mac")
        self.assertEqual(selected["uri"], "spotify:track:studio")

    def test_exact_song_request_refuses_wrong_artist(self):
        tracks = [{
            "name": "Hurt", "uri": "spotify:track:wrong",
            "artists": [{"name": "Nine Inch Nails"}],
        }]
        self.assertIsNone(self.controller._best_track_match(tracks, "Hurt", "Johnny Cash"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx


class SpotifyError(RuntimeError):
    pass


class SpotifyController:
    API = "https://api.spotify.com/v1"
    ACCOUNTS = "https://accounts.spotify.com"
    SCOPES = (
        "user-read-playback-state user-read-currently-playing user-modify-playback-state "
        "playlist-read-private playlist-read-collaborative user-library-read"
    )

    def __init__(self, client_id: str, redirect_uri: str, token_path: Path):
        self.client_id = client_id.strip()
        self.redirect_uri = redirect_uri
        self.token_path = Path(token_path)
        self._pending: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return self.token_path.exists()

    def authorization_url(self) -> str:
        if not self.configured:
            raise SpotifyError("Spotify Client ID is not configured")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._pending[state] = (verifier, time.time() + 600)
        return f"{self.ACCOUNTS}/authorize?{urlencode({'client_id': self.client_id, 'response_type': 'code', 'redirect_uri': self.redirect_uri, 'scope': self.SCOPES, 'state': state, 'code_challenge_method': 'S256', 'code_challenge': challenge})}"

    def complete_authorization(self, code: str, state: str) -> None:
        with self._lock:
            pending = self._pending.pop(state, None)
        if not pending or pending[1] < time.time():
            raise SpotifyError("Spotify connection expired; please start again")
        response = httpx.post(
            f"{self.ACCOUNTS}/api/token",
            data={"client_id": self.client_id, "grant_type": "authorization_code", "code": code,
                  "redirect_uri": self.redirect_uri, "code_verifier": pending[0]},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20,
        )
        self._check(response)
        self._save_token(response.json())

    def disconnect(self) -> None:
        if self.token_path.exists():
            self.token_path.unlink()

    def _load_token(self) -> dict:
        if not self.connected:
            raise SpotifyError("Spotify is not connected. Open /spotify/connect first.")
        return json.loads(self.token_path.read_text(encoding="utf-8"))

    def _save_token(self, token: dict) -> None:
        token = dict(token)
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token), encoding="utf-8")

    def _access_token(self) -> str:
        with self._lock:
            token = self._load_token()
            if float(token.get("expires_at", 0)) > time.time() + 60:
                return token["access_token"]
            response = httpx.post(
                f"{self.ACCOUNTS}/api/token",
                data={"client_id": self.client_id, "grant_type": "refresh_token",
                      "refresh_token": token["refresh_token"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20,
            )
            self._check(response)
            refreshed = response.json()
            refreshed.setdefault("refresh_token", token["refresh_token"])
            self._save_token(refreshed)
            return refreshed["access_token"]

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = httpx.request(method, f"{self.API}{path}", headers={"Authorization": f"Bearer {self._access_token()}"}, timeout=20, **kwargs)
        self._check(response)
        return response

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if not response.is_error:
            return
        try:
            detail = response.json().get("error", {})
            detail = detail.get("message", detail) if isinstance(detail, dict) else detail
        except Exception:
            detail = response.text
        raise SpotifyError(str(detail or f"Spotify returned HTTP {response.status_code}"))

    def playback(self) -> dict:
        response = self._request("GET", "/me/player")
        if response.status_code == 204:
            return {"playing": False, "message": "Spotify has no active playback device."}
        data = response.json()
        item = data.get("item") or {}
        artists = ", ".join(artist.get("name", "") for artist in item.get("artists", []))
        return {"playing": bool(data.get("is_playing")), "track": item.get("name"), "artist": artists,
                "device": (data.get("device") or {}).get("name")}

    def pause_if_playing(self) -> bool:
        try:
            if not self.connected or not self.playback().get("playing"):
                return False
            self._request("PUT", "/me/player/pause")
            return True
        except SpotifyError:
            return False

    def resume(self) -> None:
        self._request("PUT", "/me/player/play", json={})

    def _ensure_device(self) -> None:
        devices = self._request("GET", "/me/player/devices").json().get("devices", [])
        if any(device.get("is_active") for device in devices):
            return
        device = next((item for item in devices if not item.get("is_restricted")), None)
        if not device:
            raise SpotifyError("Open Spotify on this laptop once, then ask me again.")
        self._request("PUT", "/me/player", json={"device_ids": [device["id"]], "play": False})
        time.sleep(0.35)

    def _search(self, query: str, item_type: str, limit: int = 1) -> list[dict]:
        result = self._request(
            "GET", "/search", params={"q": query, "type": item_type, "limit": min(10, limit)}
        ).json()
        return (result.get(f"{item_type}s") or {}).get("items") or []

    @staticmethod
    def _normalized_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    @classmethod
    def _base_track_name(cls, value: str) -> str:
        # Spotify often appends edition information that the listener did not say.
        value = re.sub(r"\s*[\[(].*?[\]) ]\s*$", "", value).strip()
        value = re.sub(
            r"\s+-\s+(?:remaster(?:ed)?|live|radio edit|single version|album version)\b.*$",
            "", value, flags=re.I,
        ).strip()
        return cls._normalized_name(value)

    @classmethod
    def _best_track_match(cls, tracks: list[dict], title: str, artist: str) -> dict | None:
        wanted_title = cls._normalized_name(title)
        wanted_base = cls._base_track_name(title)
        wanted_artist = cls._normalized_name(artist)
        requested_variant = bool(re.search(
            r"\b(?:live|remix|remaster(?:ed)?|acoustic|karaoke|instrumental|cover)\b",
            title, re.I,
        ))

        best: tuple[int, int, dict] | None = None
        for track in tracks:
            if track.get("is_playable") is False:
                continue
            track_title = cls._normalized_name(track.get("name", ""))
            track_base = cls._base_track_name(track.get("name", ""))
            artist_names = [
                cls._normalized_name(item.get("name", ""))
                for item in track.get("artists", [])
            ]
            score = 0
            if track_title == wanted_title:
                score += 120
            elif track_base == wanted_base:
                score += 105
            elif wanted_title and (track_title.startswith(wanted_title) or wanted_title.startswith(track_base)):
                score += 45
            if wanted_artist in artist_names:
                score += 120
            elif any(wanted_artist in name or name in wanted_artist for name in artist_names if name):
                score += 45
            if not requested_variant and re.search(
                r"\b(?:live|remix|acoustic|karaoke|instrumental|tribute|cover)\b",
                track.get("name", ""), re.I,
            ):
                score -= 80
            popularity = int(track.get("popularity") or 0)
            candidate = (score, popularity, track)
            if best is None or candidate[:2] > best[:2]:
                best = candidate

        # Require a strong title and artist match rather than playing a plausible wrong song.
        return best[2] if best and best[0] >= 210 else None

    def _start_later(self, body: dict, shuffle: bool | None = None) -> None:
        def start() -> None:
            if shuffle is not None:
                self._request("PUT", "/me/player/shuffle", params={"state": str(shuffle).lower()})
            self._request("PUT", "/me/player/play", json=body)
        timer = threading.Timer(2.5, start)
        timer.daemon = True
        timer.start()

    @staticmethod
    def _clean_subject(text: str) -> str:
        return re.sub(
            r"\s+(?:on|from) spotify\s*$|\s+(?:on )?shuffle\s*$", "", text,
            flags=re.I,
        ).strip(" .?!\"")

    def _find_playlist(self, name: str) -> dict:
        wanted = re.sub(r"[^a-z0-9]", "", name.lower())
        candidates: list[dict] = []
        for offset in range(0, 200, 50):
            page = self._request("GET", "/me/playlists", params={"limit": 50, "offset": offset}).json()
            candidates.extend(page.get("items") or [])
            if not page.get("next"):
                break
        exact = next((item for item in candidates if re.sub(r"[^a-z0-9]", "", item.get("name", "").lower()) == wanted), None)
        if exact:
            return exact
        partial = next((item for item in candidates if wanted in re.sub(r"[^a-z0-9]", "", item.get("name", "").lower())), None)
        if partial:
            return partial
        raise SpotifyError(f"I couldn't find a playlist named {name}.")

    def command(self, command: str) -> dict:
        text = command.strip()
        lowered = text.lower()
        if not self.connected:
            raise SpotifyError("Spotify is not connected yet.")
        self._ensure_device()

        if re.search(r"\b(what|which) (song|track).*(playing|this)|what.*playing\b", lowered):
            state = self.playback()
            if not state.get("track"):
                return {"action": "status", "message": "Nothing is playing on Spotify."}
            return {"action": "status", "message": f"{state['track']} by {state['artist']}."}
        if re.search(r"\b(pause|stop)\b", lowered):
            self._request("PUT", "/me/player/pause")
            return {"action": "pause", "message": "Spotify paused."}
        if re.search(r"\b(resume|continue|unpause)\b", lowered):
            threading.Timer(2.5, self.resume).start()
            return {"action": "resume", "message": "Spotify resumed."}
        if re.search(r"\b(next|skip)\b", lowered):
            self._request("POST", "/me/player/next")
            return {"action": "next", "message": "Skipped to the next track."}
        if re.search(r"\b(previous|last song|go back)\b", lowered):
            self._request("POST", "/me/player/previous")
            return {"action": "previous", "message": "Went back one track."}
        volume = re.search(r"\b(?:volume|set (?:it|spotify)(?: volume)? to|turn (?:it|spotify) to)\s*(\d{1,3})\b", lowered)
        if volume:
            level = max(0, min(100, int(volume.group(1))))
            self._request("PUT", "/me/player/volume", params={"volume_percent": level})
            return {"action": "volume", "message": f"Spotify volume set to {level} percent."}

        liked = bool(re.search(r"\b(liked|saved) songs?\b", lowered))
        if liked:
            items = self._request("GET", "/me/tracks", params={"limit": 50}).json().get("items") or []
            uris = [((item.get("track") or item.get("item") or {}).get("uri")) for item in items]
            uris = [uri for uri in uris if uri]
            if not uris:
                raise SpotifyError("Your Spotify Liked Songs list is empty.")
            if "shuffle" in lowered or "random" in lowered:
                secrets.SystemRandom().shuffle(uris)
            self._start_later({"uris": uris})
            return {"action": "play", "message": "Playing your Liked Songs."}

        playlist_match = re.search(
            r"(?:playlist(?: called| named)?\s+(.+)|(?:play|shuffle)\s+(?:my\s+)?(.+?)\s+playlist|songs? (?:from|on)\s+(.+))$",
            text, re.I,
        )
        if playlist_match:
            playlist_name = self._clean_subject(next(group for group in playlist_match.groups() if group))
            playlist = self._find_playlist(playlist_name)
            shuffled = "shuffle" in lowered or "random" in lowered
            self._start_later({"context_uri": playlist["uri"]}, shuffle=shuffled)
            wording = "Shuffling" if shuffled else "Playing"
            return {"action": "play", "message": f"{wording} your {playlist['name']} playlist."}

        album_match = re.search(r"\b(?:album)\s+(.+)$", text, re.I)
        if album_match:
            album_name = self._clean_subject(album_match.group(1))
            albums = self._search(album_name, "album")
            if not albums:
                raise SpotifyError(f"I couldn't find the album {album_name}.")
            album = albums[0]
            self._start_later({"context_uri": album["uri"]})
            return {"action": "play", "message": f"Playing the album {album['name']}."}

        random_artist = re.search(r"\b(?:random|any) song\s+by\s+(.+)$", text, re.I)
        if random_artist:
            artist_name = self._clean_subject(random_artist.group(1))
            tracks = self._search(f'artist:"{artist_name}"', "track", limit=10)
            if not tracks:
                raise SpotifyError(f"I couldn't find songs by {artist_name}.")
            track = secrets.choice(tracks)
            self._start_later({"uris": [track["uri"]]})
            artists = ", ".join(artist["name"] for artist in track.get("artists", []))
            return {"action": "play", "message": f"Playing {track['name']} by {artists}."}

        artist_match = re.search(r"\b(?:songs?|music)\s+by\s+(.+)$|\bplay\s+(?:some\s+)?(.+?)\s+(?:songs|music)\s*$", text, re.I)
        if artist_match:
            artist_name = self._clean_subject(next(group for group in artist_match.groups() if group))
            artists = self._search(artist_name, "artist")
            if not artists:
                raise SpotifyError(f"I couldn't find the artist {artist_name}.")
            artist = artists[0]
            self._start_later({"context_uri": artist["uri"]}, shuffle=True)
            return {"action": "play", "message": f"Shuffling songs by {artist['name']}."}

        queue_request = bool(re.search(r"\b(?:play .+ next|add .+ to (?:the )?queue|queue .+)\b", lowered))
        query = re.sub(r"^.*?\bplay\b\s+", "", text, flags=re.I).strip()
        if queue_request and query == text:
            query = re.sub(r"^(?:add|queue)\s+", "", text, flags=re.I).strip()
        query = re.sub(r"\s+(?:on|from) spotify\s*$", "", query, flags=re.I).strip()
        query = re.sub(r"\s+(?:to (?:the )?queue|next)$", "", query, flags=re.I).strip()
        if not query or (query == text and not queue_request):
            raise SpotifyError("Tell me what you want Spotify to play.")

        exact_request = re.fullmatch(
            r"(?:the song\s+)?(.+?)\s+by\s+(.+)", query, re.I,
        )
        if exact_request:
            title = self._clean_subject(exact_request.group(1))
            artist = self._clean_subject(exact_request.group(2))
            safe_title = title.replace('"', "")
            safe_artist = artist.replace('"', "")
            items = self._search(
                f'track:"{safe_title}" artist:"{safe_artist}"', "track", limit=10,
            )
            track = self._best_track_match(items, title, artist)
            if track is None:
                raise SpotifyError(
                    f"I couldn't confidently match {title} by {artist}. Please repeat the exact title and artist."
                )
        else:
            items = self._search(query, "track", limit=10)
            track = items[0] if items else None
        if not items:
            raise SpotifyError(f"I couldn't find {query} on Spotify.")
        if track is None:
            raise SpotifyError(f"I couldn't find {query} on Spotify.")
        artists = ", ".join(artist["name"] for artist in track.get("artists", []))
        if queue_request:
            self._request("POST", "/me/player/queue", params={"uri": track["uri"]})
            return {"action": "queue", "message": f"Queued {track['name']} by {artists}."}
        self._start_later({"uris": [track["uri"]]})
        return {"action": "play", "message": f"Playing {track['name']} by {artists}."}

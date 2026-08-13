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
    SCOPES = "user-read-playback-state user-read-currently-playing user-modify-playback-state"

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
        query = re.sub(r"^.*?\bplay\b\s+", "", text, flags=re.I).strip()
        query = re.sub(r"\s+(?:on|from) spotify\s*$", "", query, flags=re.I).strip()
        if not query or query == text:
            raise SpotifyError("Tell me what you want Spotify to play.")
        result = self._request("GET", "/search", params={"q": query, "type": "track", "limit": 1}).json()
        items = (result.get("tracks") or {}).get("items") or []
        if not items:
            raise SpotifyError(f"I couldn't find {query} on Spotify.")
        track = items[0]
        timer = threading.Timer(
            2.5, lambda: self._request("PUT", "/me/player/play", json={"uris": [track["uri"]]})
        )
        timer.daemon = True
        timer.start()
        artists = ", ".join(artist["name"] for artist in track.get("artists", []))
        return {"action": "play", "message": f"Playing {track['name']} by {artists}."}

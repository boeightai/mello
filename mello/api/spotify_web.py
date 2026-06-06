"""
Spotify Web API client and token storage helpers.

This module provides the credential/cache foundation for Spotify Web API
features while remaining fully testable with a mocked requests session.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS_BASE_URL = "https://accounts.spotify.com"
DEFAULT_SPOTIFY_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-read-playback-state",
    "user-modify-playback-state",
)


class SpotifyWebAPIError(RuntimeError):
    """Raised when the Spotify Web API returns an unsuccessful response."""


def generate_pkce_verifier(length: int = 64) -> str:
    """Generate a PKCE code verifier."""
    return secrets.token_urlsafe(length)[:128]


def pkce_challenge(verifier: str) -> str:
    """Generate a Spotify-compatible S256 PKCE challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    scopes: Optional[List[str]] = None,
    state: Optional[str] = None,
    accounts_base_url: str = SPOTIFY_ACCOUNTS_BASE_URL,
) -> str:
    """Build a Spotify Authorization Code with PKCE authorize URL."""
    if not client_id:
        raise SpotifyWebAPIError("Spotify client ID is not configured")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes or list(DEFAULT_SPOTIFY_SCOPES)),
        "code_challenge_method": "S256",
        "code_challenge": pkce_challenge(code_verifier),
    }
    if state:
        params["state"] = state
    return f"{accounts_base_url.rstrip('/')}/authorize?{urlencode(params)}"


def _token_from_response(data: dict, previous_refresh_token: Optional[str] = None) -> SpotifyToken:
    token_data = dict(data)
    if previous_refresh_token and not token_data.get("refresh_token"):
        token_data["refresh_token"] = previous_refresh_token
    token = SpotifyToken.from_dict(token_data)
    if not token:
        raise SpotifyWebAPIError("Spotify token response did not include an access token")
    return token


def exchange_authorization_code(
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    session: Optional[requests.Session] = None,
    accounts_base_url: str = SPOTIFY_ACCOUNTS_BASE_URL,
) -> SpotifyToken:
    """Exchange a PKCE authorization code for a Spotify token."""
    if not client_id:
        raise SpotifyWebAPIError("Spotify client ID is not configured")
    req = session or requests.Session()
    resp = req.post(
        f"{accounts_base_url.rstrip('/')}/api/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise SpotifyWebAPIError(f"Spotify authorization code exchange failed: {getattr(resp, 'text', '')}")
    return _token_from_response(resp.json())


def refresh_access_token(
    token: SpotifyToken,
    client_id: str,
    session: Optional[requests.Session] = None,
    accounts_base_url: str = SPOTIFY_ACCOUNTS_BASE_URL,
) -> SpotifyToken:
    """Refresh a Spotify access token using the PKCE refresh-token flow."""
    if not client_id:
        raise SpotifyWebAPIError("Spotify client ID is not configured")
    if not token.refresh_token:
        raise SpotifyWebAPIError("Spotify refresh token is not configured")
    req = session or requests.Session()
    resp = req.post(
        f"{accounts_base_url.rstrip('/')}/api/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise SpotifyWebAPIError(f"Spotify token refresh failed: {getattr(resp, 'text', '')}")
    return _token_from_response(resp.json(), previous_refresh_token=token.refresh_token)


@dataclass
class SpotifyToken:
    """Stored Spotify OAuth token data."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    token_type: str = "Bearer"
    scope: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> Optional["SpotifyToken"]:
        """Build a token from JSON/env-style data."""
        if not isinstance(data, dict):
            return None

        access_token = data.get("access_token")
        if not access_token:
            return None

        expires_at = data.get("expires_at")
        if expires_at is None and data.get("expires_in") is not None:
            try:
                expires_at = time.time() + float(data["expires_in"])
            except (TypeError, ValueError):
                expires_at = None

        try:
            expires_at = float(expires_at) if expires_at is not None else None
        except (TypeError, ValueError):
            expires_at = None

        return cls(
            access_token=str(access_token),
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            token_type=data.get("token_type") or "Bearer",
            scope=data.get("scope"),
        )

    def to_dict(self) -> dict:
        """Serialize token data for local JSON storage."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "scope": self.scope,
        }

    def is_expired(self, skew_seconds: int = 60) -> bool:
        """Return True when the access token should be refreshed before use."""
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at - skew_seconds


def load_token_from_env(prefix: str = "SPOTIFY_") -> Optional[SpotifyToken]:
    """Load token data from environment variables.

    Supported names:
    - SPOTIFY_ACCESS_TOKEN
    - SPOTIFY_REFRESH_TOKEN
    - SPOTIFY_TOKEN_EXPIRES_AT
    - SPOTIFY_TOKEN_EXPIRES_IN
    - SPOTIFY_TOKEN_TYPE
    - SPOTIFY_SCOPE
    """
    data = {
        "access_token": os.getenv(f"{prefix}ACCESS_TOKEN"),
        "refresh_token": os.getenv(f"{prefix}REFRESH_TOKEN"),
        "expires_at": os.getenv(f"{prefix}TOKEN_EXPIRES_AT"),
        "expires_in": os.getenv(f"{prefix}TOKEN_EXPIRES_IN"),
        "token_type": os.getenv(f"{prefix}TOKEN_TYPE"),
        "scope": os.getenv(f"{prefix}SCOPE"),
    }
    return SpotifyToken.from_dict(data)


def load_token_from_json(path: Path) -> Optional[SpotifyToken]:
    """Load token data from a JSON file."""
    try:
        if not path.exists():
            return None
        return SpotifyToken.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load Spotify token JSON from {path}: {e}")
        return None


def save_token_to_json(token: SpotifyToken, path: Path):
    """Save token data atomically to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(token.to_dict(), indent=2))
    temp_path.chmod(0o600)
    os.replace(temp_path, path)
    path.chmod(0o600)


def load_token(
    token_path: Optional[Path] = None,
    env_prefix: str = "SPOTIFY_",
    prefer_env: bool = True,
) -> Optional[SpotifyToken]:
    """Load token data from env and/or a local JSON file."""
    env_token = load_token_from_env(env_prefix)
    json_token = load_token_from_json(token_path) if token_path else None
    return (env_token or json_token) if prefer_env else (json_token or env_token)


class SpotifyWebAPI:
    """Small Spotify Web API client for player and playlist operations."""

    def __init__(
        self,
        token: Optional[SpotifyToken] = None,
        token_path: Optional[Path] = None,
        session: Optional[requests.Session] = None,
        base_url: str = SPOTIFY_API_BASE_URL,
        token_refresher: Optional[Callable[[SpotifyToken], SpotifyToken]] = None,
    ):
        self.token = token
        self.token_path = token_path
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.token_refresher = token_refresher

    @classmethod
    def from_env_or_json(
        cls,
        token_path: Optional[Path] = None,
        env_prefix: str = "SPOTIFY_",
        prefer_env: bool = True,
        **kwargs,
    ) -> "SpotifyWebAPI":
        """Create a client using env token data, falling back to JSON storage."""
        return cls(
            token=load_token(token_path, env_prefix, prefer_env=prefer_env),
            token_path=token_path,
            **kwargs,
        )

    def _refresh_if_needed(self):
        if not self.token or not self.token.is_expired() or not self.token_refresher:
            return

        self.token = self.token_refresher(self.token)
        if self.token_path:
            save_token_to_json(self.token, self.token_path)

    def _headers(self) -> Dict[str, str]:
        if not self.token or not self.token.access_token:
            raise SpotifyWebAPIError("Spotify access token is not configured")
        return {"Authorization": f"{self.token.token_type} {self.token.access_token}"}

    def _url(self, endpoint_or_url: str) -> str:
        if endpoint_or_url.startswith("http://") or endpoint_or_url.startswith("https://"):
            return endpoint_or_url
        return f"{self.base_url}/{endpoint_or_url.lstrip('/')}"

    def _request(self, method: str, endpoint_or_url: str, **kwargs):
        self._refresh_if_needed()
        headers = kwargs.pop("headers", {})
        headers.update(self._headers())
        timeout = kwargs.pop("timeout", 10)
        kwargs = {key: value for key, value in kwargs.items() if value is not None}

        resp = self.session.request(
            method,
            self._url(endpoint_or_url),
            headers=headers,
            timeout=timeout,
            **kwargs,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 204:
            return None

        message = getattr(resp, "text", "") or f"HTTP {resp.status_code}"
        raise SpotifyWebAPIError(f"Spotify Web API {method} {endpoint_or_url} failed: {message}")

    def _paged(self, endpoint_or_url: str, params: Optional[dict] = None) -> Iterator[dict]:
        next_url = endpoint_or_url
        next_params = params
        while next_url:
            data = self._request("GET", next_url, params=next_params)
            for item in data.get("items", []):
                yield item
            next_url = data.get("next")
            next_params = None

    def current_playback(self) -> Optional[dict]:
        """Return the current playback payload, or None when Spotify returns 204."""
        return self._request("GET", "/me/player")

    def pause_playback(self, device_id: Optional[str] = None) -> bool:
        """Pause Spotify playback, optionally targeting a Connect device."""
        params = {"device_id": device_id} if device_id else None
        self._request("PUT", "/me/player/pause", params=params)
        return True

    def available_devices(self) -> List[dict]:
        """Return available Spotify Connect devices."""
        data = self._request("GET", "/me/player/devices")
        return data.get("devices", []) if isinstance(data, dict) else []

    def current_user_playlists(self, limit: int = 50) -> List[dict]:
        """Return all current-user playlists by following Spotify pagination."""
        return list(self._paged("/me/playlists", params={"limit": limit}))

    def playlist_items(self, playlist_id: str, limit: int = 100) -> List[dict]:
        """Return all playlist item rows by following Spotify pagination."""
        endpoint = f"/playlists/{playlist_id}/items"
        return list(self._paged(endpoint, params={"limit": limit, "additional_types": "track"}))

    def start_playlist_track_on_device(
        self,
        playlist_uri: str,
        track_uri: str,
        device_id: str,
        position_ms: int = 0,
    ) -> bool:
        """Start a specific playlist track on a Spotify Connect device."""
        self._request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id},
            json={
                "context_uri": playlist_uri,
                "offset": {"uri": track_uri},
                "position_ms": position_ms,
            },
        )
        return True

    def transfer_playback(self, device_id: str, play: bool = False) -> bool:
        """Transfer Spotify playback to a Connect device."""
        self._request(
            "PUT",
            "/me/player",
            json={"device_ids": [device_id], "play": play},
        )
        return True

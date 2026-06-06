"""
Tests for Spotify Web API token helpers and client behavior.
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from mello.api.spotify_web import (
    SpotifyToken,
    SpotifyWebAPI,
    SpotifyWebAPIError,
    load_token,
    load_token_from_env,
    load_token_from_json,
    save_token_to_json,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def make_client(token=None, session=None, **kwargs):
    return SpotifyWebAPI(
        token=token or SpotifyToken(access_token="access"),
        session=session or MagicMock(),
        **kwargs,
    )


def test_token_round_trip_to_json(tmp_path):
    token_path = tmp_path / "spotify-token.json"
    token = SpotifyToken(
        access_token="access",
        refresh_token="refresh",
        expires_at=12345,
        scope="user-read-playback-state",
    )

    save_token_to_json(token, token_path)
    loaded = load_token_from_json(token_path)

    assert loaded == token
    assert not (tmp_path / "spotify-token.json.tmp").exists()


def test_load_token_from_env(monkeypatch):
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "env-access")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "env-refresh")
    monkeypatch.setenv("SPOTIFY_TOKEN_EXPIRES_AT", "123")
    monkeypatch.setenv("SPOTIFY_SCOPE", "playlist-read-private")

    token = load_token_from_env()

    assert token == SpotifyToken(
        access_token="env-access",
        refresh_token="env-refresh",
        expires_at=123.0,
        scope="playlist-read-private",
    )


def test_load_token_prefers_env_over_json(monkeypatch, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "json-access"}))
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "env-access")

    token = load_token(token_path)

    assert token.access_token == "env-access"


def test_token_expires_in_becomes_expires_at():
    before = time.time()
    token = SpotifyToken.from_dict({"access_token": "access", "expires_in": "3600"})

    assert token.expires_at >= before + 3600
    assert token.expires_at <= time.time() + 3600


def test_current_playback_returns_none_on_204():
    session = MagicMock()
    session.request.return_value = FakeResponse(status_code=204)
    client = make_client(session=session)

    assert client.current_playback() is None


def test_available_devices_returns_devices_and_auth_header():
    session = MagicMock()
    session.request.return_value = FakeResponse(payload={"devices": [{"id": "pi"}]})
    client = make_client(session=session)

    devices = client.available_devices()

    assert devices == [{"id": "pi"}]
    session.request.assert_called_once_with(
        "GET",
        "https://api.spotify.com/v1/me/player/devices",
        headers={"Authorization": "Bearer access"},
        timeout=10,
    )


def test_current_user_playlists_follows_pagination():
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(payload={
            "items": [{"id": "one"}],
            "next": "https://api.spotify.com/v1/me/playlists?offset=50",
        }),
        FakeResponse(payload={"items": [{"id": "two"}], "next": None}),
    ]
    client = make_client(session=session)

    playlists = client.current_user_playlists(limit=50)

    assert playlists == [{"id": "one"}, {"id": "two"}]
    assert session.request.call_args_list[0].kwargs["params"] == {"limit": 50}
    assert "params" not in session.request.call_args_list[1].kwargs


def test_playlist_items_follows_pagination():
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(payload={"items": [{"track": {"uri": "spotify:track:1"}}], "next": "/next"}),
        FakeResponse(payload={"items": [{"track": {"uri": "spotify:track:2"}}], "next": None}),
    ]
    client = make_client(session=session)

    items = client.playlist_items("playlist-id", limit=2)

    assert [item["track"]["uri"] for item in items] == ["spotify:track:1", "spotify:track:2"]
    first_call = session.request.call_args_list[0]
    assert first_call.args[1] == "https://api.spotify.com/v1/playlists/playlist-id/tracks"
    assert first_call.kwargs["params"] == {"limit": 2}


def test_start_playlist_track_on_device_puts_expected_body():
    session = MagicMock()
    session.request.return_value = FakeResponse(status_code=204)
    client = make_client(session=session)

    result = client.start_playlist_track_on_device(
        playlist_uri="spotify:playlist:abc",
        track_uri="spotify:track:def",
        device_id="device-1",
        position_ms=1500,
    )

    assert result is True
    session.request.assert_called_once_with(
        "PUT",
        "https://api.spotify.com/v1/me/player/play",
        headers={"Authorization": "Bearer access"},
        timeout=10,
        params={"device_id": "device-1"},
        json={
            "context_uri": "spotify:playlist:abc",
            "offset": {"uri": "spotify:track:def"},
            "position_ms": 1500,
        },
    )


def test_transfer_playback_puts_expected_body():
    session = MagicMock()
    session.request.return_value = FakeResponse(status_code=204)
    client = make_client(session=session)

    result = client.transfer_playback(device_id="device-1", play=True)

    assert result is True
    session.request.assert_called_once_with(
        "PUT",
        "https://api.spotify.com/v1/me/player",
        headers={"Authorization": "Bearer access"},
        timeout=10,
        json={"device_ids": ["device-1"], "play": True},
    )


def test_refresh_callback_updates_token_and_cache(tmp_path):
    token_path = tmp_path / "token.json"
    expired = SpotifyToken(access_token="old", refresh_token="refresh", expires_at=1)
    refreshed = SpotifyToken(access_token="new", refresh_token="refresh", expires_at=time.time() + 3600)
    session = MagicMock()
    session.request.return_value = FakeResponse(payload={"devices": []})
    refresher = MagicMock(return_value=refreshed)
    client = make_client(
        token=expired,
        token_path=token_path,
        session=session,
        token_refresher=refresher,
    )

    client.available_devices()

    refresher.assert_called_once_with(expired)
    assert json.loads(token_path.read_text())["access_token"] == "new"
    assert session.request.call_args.kwargs["headers"] == {"Authorization": "Bearer new"}


def test_api_error_includes_response_text():
    session = MagicMock()
    session.request.return_value = FakeResponse(status_code=401, text="unauthorized")
    client = make_client(session=session)

    with pytest.raises(SpotifyWebAPIError, match="unauthorized"):
        client.available_devices()

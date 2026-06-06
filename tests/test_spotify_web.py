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
    DEFAULT_SPOTIFY_SCOPES,
    SpotifyToken,
    SpotifyWebAPI,
    SpotifyWebAPIError,
    build_authorize_url,
    exchange_authorization_code,
    pkce_challenge,
    refresh_access_token,
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


class FakeNonJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("not json")


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
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"


def test_pkce_challenge_uses_s256_urlsafe_base64():
    challenge = pkce_challenge("verifier")

    assert challenge == "iMnq5o6zALKXGivsnlom_0F5_WYda32GHkxlV7mq7hQ"
    assert "=" not in challenge


def test_build_authorize_url_includes_required_pkce_params():
    url = build_authorize_url(
        client_id="client",
        redirect_uri="http://127.0.0.1:8765/callback",
        code_verifier="verifier",
        state="state",
    )

    assert url.startswith("https://accounts.spotify.com/authorize?")
    assert "client_id=client" in url
    assert "response_type=code" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback" in url
    assert "code_challenge_method=S256" in url
    assert "state=state" in url
    for scope in DEFAULT_SPOTIFY_SCOPES:
        assert scope in url


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


def test_load_token_can_prefer_json_over_env(monkeypatch, tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "json-access"}))
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "env-access")

    token = load_token(token_path, prefer_env=False)

    assert token.access_token == "json-access"


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
        FakeResponse(payload={"items": [{"item": {"uri": "spotify:track:1"}}], "next": "/next"}),
        FakeResponse(payload={"items": [{"item": {"uri": "spotify:track:2"}}], "next": None}),
    ]
    client = make_client(session=session)

    items = client.playlist_items("playlist-id", limit=2)

    assert [item["item"]["uri"] for item in items] == ["spotify:track:1", "spotify:track:2"]
    first_call = session.request.call_args_list[0]
    assert first_call.args[1] == "https://api.spotify.com/v1/playlists/playlist-id/items"
    assert first_call.kwargs["params"] == {"limit": 2, "additional_types": "track"}


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


def test_start_playlist_track_on_device_retries_unsupported_playlist_uri_with_track_uri():
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(
            status_code=400,
            text='{"error":{"message":"Unsupported uri kind: playlist_v2"}}',
        ),
        FakeResponse(status_code=204),
    ]
    client = make_client(session=session)

    result = client.start_playlist_track_on_device(
        playlist_uri="spotify:playlist:abc",
        track_uri="spotify:track:def",
        device_id="device-1",
        position_ms=1500,
    )

    assert result is True
    assert session.request.call_count == 2
    retry_call = session.request.call_args_list[1]
    assert retry_call.args[:2] == (
        "PUT",
        "https://api.spotify.com/v1/me/player/play",
    )
    assert retry_call.kwargs["json"] == {
        "uris": ["spotify:track:def"],
        "position_ms": 1500,
    }


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


def test_exchange_authorization_code_posts_pkce_body():
    session = MagicMock()
    session.post.return_value = FakeResponse(payload={
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
        "scope": "playlist-read-private",
    })

    token = exchange_authorization_code(
        client_id="client",
        redirect_uri="http://127.0.0.1:8765/callback",
        code="code",
        code_verifier="verifier",
        session=session,
        accounts_base_url="https://accounts.test",
    )

    assert token.access_token == "new-access"
    assert token.refresh_token == "new-refresh"
    session.post.assert_called_once_with(
        "https://accounts.test/api/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": "client",
            "grant_type": "authorization_code",
            "code": "code",
            "redirect_uri": "http://127.0.0.1:8765/callback",
            "code_verifier": "verifier",
        },
        timeout=10,
    )


def test_refresh_access_token_posts_refresh_body_and_preserves_refresh_token():
    session = MagicMock()
    session.post.return_value = FakeResponse(payload={
        "access_token": "new-access",
        "expires_in": 3600,
    })
    old = SpotifyToken(access_token="old", refresh_token="refresh")

    token = refresh_access_token(
        old,
        client_id="client",
        session=session,
        accounts_base_url="https://accounts.test",
    )

    assert token.access_token == "new-access"
    assert token.refresh_token == "refresh"
    session.post.assert_called_once_with(
        "https://accounts.test/api/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": "client",
            "grant_type": "refresh_token",
            "refresh_token": "refresh",
        },
        timeout=10,
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


def test_pause_playback_targets_device():
    session = MagicMock()
    session.request.return_value = FakeResponse(status_code=204)
    client = make_client(session=session)

    assert client.pause_playback(device_id="mello-device") is True
    session.request.assert_called_once()
    assert session.request.call_args.args[:2] == (
        "PUT",
        "https://api.spotify.com/v1/me/player/pause",
    )
    assert session.request.call_args.kwargs["params"] == {"device_id": "mello-device"}


def test_pause_playback_accepts_successful_non_json_response():
    session = MagicMock()
    session.request.return_value = FakeNonJsonResponse(status_code=200, text="ok")
    client = make_client(session=session)

    assert client.pause_playback(device_id="mello-device") is True

"""
Tests for Mello list-mode app routing.
"""
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

pygame_stub = types.ModuleType('pygame')
pygame_stub.Surface = object
pygame_stub.Rect = object
pygame_stub.font = SimpleNamespace(Font=object)
pygame_stub.K_ESCAPE = 27
pygame_stub.K_BACKSPACE = 8
pygame_stub.K_r = ord('r')
pygame_stub.K_l = ord('l')
sys.modules.setdefault('pygame', pygame_stub)
sys.modules.setdefault('pygame.gfxdraw', types.ModuleType('pygame.gfxdraw'))

from mello.app import Mello
from mello.models import NowPlaying, SpotifyPlaylist, SpotifyPlaylistTrack


def _playlist(playlist_id='p1', name='Family Mix'):
    return SpotifyPlaylist(
        id=playlist_id,
        uri=f'spotify:playlist:{playlist_id}',
        name=name,
        track_count=2,
        owner_name='Bo',
    )


def _track(track_id='t1', name='Song One'):
    return SpotifyPlaylistTrack(
        id=track_id,
        uri=f'spotify:track:{track_id}',
        name=name,
        artists=['Artist'],
        position=0,
    )


def _make_app(mode='playlists'):
    playlist = _playlist()
    track = _track()
    app = Mello.__new__(Mello)
    app.list_mode_enabled = True
    app.ui_mode = mode
    app._selected_playlist_id = playlist.id
    app._playlist_scroll_offset = 0
    app._track_scroll_offset = 0
    app._list_touch_start = None
    app._list_touch_last = None
    app._list_touch_scrolled = False
    app._pressed_list_index = None
    app._spotify_refresh_in_progress = False
    app._last_action_time = 0
    app._user_activated_playback = False
    app._manual_pause_lock = False
    app._manual_pause_context_uri = None
    app._now_playing_lock = threading.Lock()
    app._now_playing = NowPlaying()
    app.spotify_client = SimpleNamespace(token=None)
    app.spotify_library = SimpleNamespace(
        playlists=[playlist],
        tracks_for_playlist=lambda playlist_id: [track],
        refresh_playlist_tracks=MagicMock(return_value=[track]),
        refresh_playlists=MagicMock(return_value=[playlist]),
    )
    app.renderer = SimpleNamespace(
        playlist_row_rects=[(10, 10, 50, 50)],
        track_row_rects=[(10, 10, 50, 50)],
        playlist_back_rect=(100, 100, 40, 40),
        playlist_settings_rect=None,
        track_back_rect=(100, 100, 40, 40),
        draw_playlist_list=MagicMock(),
        draw_track_list=MagicMock(),
        invalidate=MagicMock(),
    )
    app.setup_menu = SimpleNamespace(is_open=False, open=MagicMock())
    app.touch = SimpleNamespace(on_down=MagicMock())
    app.user_interacting = False
    app.delete_mode_id = None
    app.api = SimpleNamespace(
        status=MagicMock(return_value={'device_id': 'local-device'}),
        play=MagicMock(return_value=True),
    )
    app.volume = SimpleNamespace(unmute=MagicMock())
    app.playback = SimpleNamespace(
        play_state=SimpleNamespace(start_loading=MagicMock()),
    )
    app._show_toast = MagicMock()
    app._handle_button_tap = MagicMock()
    app._clear_manual_pause_lock = MagicMock()
    app._last_play_commit_uri = None
    app._last_play_commit_at = 0
    return app


def test_playlist_row_tap_enters_track_list_without_carousel_fallthrough():
    app = _make_app('playlists')

    app._handle_touch_down((20, 20))
    app._handle_list_touch_up((20, 20))

    assert app.ui_mode == 'tracks'
    assert app._selected_playlist_id == 'p1'
    app.touch.on_down.assert_not_called()
    app._handle_button_tap.assert_not_called()
    app.renderer.invalidate.assert_called()


def test_track_row_tap_plays_track_with_local_fallback():
    app = _make_app('tracks')

    with patch('mello.app.run_async') as mock_run:
        mock_run.side_effect = lambda fn, *args: fn(*args)
        app._handle_touch_down((20, 20))
        app._handle_list_touch_up((20, 20))

    app.volume.unmute.assert_called_once()
    app.playback.play_state.start_loading.assert_called_once()
    app.api.play.assert_called_once_with('spotify:playlist:p1', skip_to_uri='spotify:track:t1')
    app.touch.on_down.assert_not_called()
    app._handle_button_tap.assert_not_called()


def test_track_back_tap_returns_to_playlist_list():
    app = _make_app('tracks')

    app._handle_touch_down((110, 110))
    app._handle_list_touch_up((110, 110))

    assert app.ui_mode == 'playlists'
    app.renderer.invalidate.assert_called()


def test_draw_list_mode_uses_track_renderer():
    app = _make_app('tracks')

    result = app._draw_list_mode()

    assert result is None
    app.renderer.draw_track_list.assert_called_once()
    args = app.renderer.draw_track_list.call_args.args
    assert args[0].uri == 'spotify:playlist:p1'
    assert args[1][0]['uri'] == 'spotify:track:t1'


def test_list_drag_scrolls_without_row_tap():
    app = _make_app('tracks')
    app.spotify_library = SimpleNamespace(
        playlists=[_playlist()],
        tracks_for_playlist=lambda playlist_id: [_track(str(i), f'Song {i}') for i in range(20)],
        refresh_playlist_tracks=MagicMock(),
        refresh_playlists=MagicMock(),
    )
    app.renderer._LIST_ROW_H = 82
    app.renderer._LIST_ROW_GAP = 10
    app.renderer._LIST_ROW_X = 560

    app._handle_touch_down((20, 20))
    app._handle_list_motion((120, 20))
    app._handle_list_touch_up((120, 20))

    assert app._track_scroll_offset == 100
    app.api.play.assert_not_called()
    app.renderer.invalidate.assert_called()


def test_list_scroll_offset_is_clamped():
    app = _make_app('playlists')
    app.spotify_library = SimpleNamespace(
        playlists=[_playlist(str(i), f'Playlist {i}') for i in range(20)],
        tracks_for_playlist=lambda playlist_id: [],
        refresh_playlist_tracks=MagicMock(),
        refresh_playlists=MagicMock(),
    )
    app.renderer._LIST_ROW_H = 82
    app.renderer._LIST_ROW_GAP = 10
    app.renderer._LIST_ROW_X = 560

    app._set_list_scroll_offset(10_000)

    assert app._playlist_scroll_offset == (20 - 1) * (82 + 10) - 560


def test_spotify_library_refresh_skips_unreadable_selected_playlist():
    app = _make_app('tracks')
    unreadable = _playlist('blocked', 'Blocked')
    readable = _playlist('good', 'Good')
    app._selected_playlist_id = unreadable.id
    app.spotify_client = SimpleNamespace(token=object())

    def refresh_tracks(playlist_id):
        if playlist_id == unreadable.id:
            raise RuntimeError('forbidden')
        return [_track()]

    app.spotify_library = SimpleNamespace(
        playlists=[unreadable, readable],
        tracks_for_playlist=lambda playlist_id: [],
        refresh_playlist_tracks=MagicMock(side_effect=refresh_tracks),
        refresh_playlists=MagicMock(return_value=[unreadable, readable]),
    )

    with patch('mello.app.run_async') as mock_run:
        mock_run.side_effect = lambda fn, *args: fn(*args)
        app._refresh_spotify_library()

    assert app._selected_playlist_id == readable.id
    assert app.spotify_library.refresh_playlist_tracks.call_args_list[0].args == (unreadable.id,)
    assert app.spotify_library.refresh_playlist_tracks.call_args_list[1].args == (readable.id,)
    app._show_toast.assert_not_called()
    app.renderer.invalidate.assert_called()

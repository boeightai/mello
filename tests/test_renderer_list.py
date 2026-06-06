"""
Tests for Renderer list-mode primitives.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
pygame = pytest.importorskip('pygame')
if not hasattr(pygame, 'init') or type(pygame.font).__name__ == 'MissingModule':
    pytest.skip('pygame font unavailable', allow_module_level=True)

from mello.config import COLORS, SCREEN_HEIGHT, SCREEN_WIDTH
from mello.models import CatalogItem, NowPlaying
from mello.ui.renderer import Renderer


class DummyImageCache:
    def get(self, image, size):
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        surf.fill((20, 40, 60))
        return surf


def _renderer():
    pygame.init()
    pygame.display.set_mode((1, 1))
    screen = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    return Renderer(screen, DummyImageCache(), {})


def _playlist(item_id='1', uri='spotify:playlist:one', name='Playlist'):
    return CatalogItem(id=item_id, uri=uri, name=name, type='playlist')


def test_playlist_list_handles_long_names_and_populates_hit_rects():
    renderer = _renderer()
    long_name = 'Saturday Morning Pancakes Dance Party With A Very Long Family Name'

    renderer.draw_playlist_list([_playlist(name=long_name)])

    assert len(renderer.playlist_row_rects) == 1
    rect = renderer.playlist_row_rects[0]
    assert rect is not None
    assert rect.width >= 1000
    assert rect.height >= 80
    assert 0 <= rect.left < SCREEN_WIDTH
    assert 0 < rect.right <= SCREEN_WIDTH


def test_playlist_empty_state_resets_hit_rects():
    renderer = _renderer()
    renderer.draw_playlist_list([_playlist()])
    assert renderer.playlist_row_rects

    renderer.draw_playlist_list([])

    assert renderer.playlist_row_rects == []
    assert renderer.track_row_rects == []


def test_track_list_populates_rows_and_back_rect():
    renderer = _renderer()
    playlist = _playlist(name='Family Station')
    tracks = [
        {'uri': 'spotify:track:one', 'name': 'First Track', 'artist': 'Artist One'},
        {'uri': 'spotify:track:two', 'name': 'Second Track', 'artist': 'Artist Two'},
    ]

    renderer.draw_track_list(playlist, tracks, NowPlaying())

    assert len(renderer.track_row_rects) == 2
    assert renderer.track_row_rects[0] is not None
    assert renderer.track_row_rects[1] is not None
    assert renderer.track_back_rect is not None
    assert renderer.playlist_row_rects == []


def test_track_list_highlights_current_track():
    renderer = _renderer()
    tracks = [
        {'uri': 'spotify:track:one', 'name': 'First Track', 'artist': 'Artist One'},
        {'uri': 'spotify:track:two', 'name': 'Second Track', 'artist': 'Artist Two'},
    ]
    now = NowPlaying(track_uri='spotify:track:two')

    renderer.draw_track_list(_playlist(), tracks, now)

    highlighted = renderer.track_row_rects[1]
    assert highlighted is not None
    sample = renderer.screen.get_at((highlighted.centerx, highlighted.bottom - 8))[:3]
    assert sample == COLORS['accent']


def test_track_empty_state_resets_rows_and_keeps_back_rect():
    renderer = _renderer()
    renderer.draw_track_list(_playlist(), [{'uri': 'spotify:track:one', 'name': 'One'}], NowPlaying())
    assert renderer.track_row_rects

    renderer.draw_track_list(_playlist(), [], NowPlaying())

    assert renderer.track_row_rects == []
    assert renderer.playlist_row_rects == []
    assert renderer.track_back_rect is not None

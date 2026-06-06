"""
Tests for SpotifyLibraryManager typed fetch/cache behavior.
"""
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from mello.api.spotify_library import SpotifyLibraryManager


class FakeSpotifyWebAPI:
    def __init__(self, playlist_pages=None, track_pages=None):
        self.playlist_pages = playlist_pages or []
        self.track_pages = track_pages or {}
        self.playlist_calls = 0
        self.track_calls = []

    def current_user_playlists(self, limit=50):
        self.playlist_calls += 1
        return [item for page in self.playlist_pages for item in page]

    def playlist_items(self, playlist_id, limit=100):
        self.track_calls.append(playlist_id)
        return [item for page in self.track_pages.get(playlist_id, []) for item in page]


def playlist_payload(playlist_id, name, total=0, image=None):
    return {
        'id': playlist_id,
        'uri': f'spotify:playlist:{playlist_id}',
        'name': name,
        'tracks': {'total': total},
        'images': [{'url': image}] if image else [],
        'owner': {'display_name': 'Mello'},
        'snapshot_id': f'snap-{playlist_id}',
    }


def track_item(track_id, name, artist='Artist', playable=True, position_added='2026-06-01T00:00:00Z'):
    return {
        'added_at': position_added,
        'added_by': {'id': 'user-1'},
        'track': {
            'id': track_id,
            'uri': f'spotify:track:{track_id}',
            'type': 'track',
            'name': name,
            'artists': [{'name': artist}],
            'album': {
                'name': 'Album',
                'images': [{'url': f'https://img/{track_id}.jpg'}],
            },
            'duration_ms': 123000,
            'is_playable': playable,
        },
    }


def test_refresh_all_uses_paginated_fake_results_and_preserves_order(tmp_path):
    client = FakeSpotifyWebAPI(
        playlist_pages=[
            [playlist_payload('p1', 'Morning', total=2)],
            [playlist_payload('p2', 'Bedtime', total=1)],
        ],
        track_pages={
            'p1': [[track_item('t1', 'One')], [track_item('t2', 'Two')]],
            'p2': [[track_item('t3', 'Three')]],
        },
    )
    manager = SpotifyLibraryManager(client, tmp_path / 'spotify-library.json')

    playlists = manager.refresh_all()

    assert [playlist.id for playlist in playlists] == ['p1', 'p2']
    assert [track.uri for track in manager.tracks_for_playlist('p1')] == [
        'spotify:track:t1',
        'spotify:track:t2',
    ]
    assert [track.position for track in manager.tracks_for_playlist('p1')] == [0, 1]
    assert client.track_calls == ['p1', 'p2']


def test_cache_round_trip_loads_playlists_and_tracks_on_startup(tmp_path):
    cache_path = tmp_path / 'spotify-library.json'
    client = FakeSpotifyWebAPI(
        playlist_pages=[[playlist_payload('p1', 'Morning', total=1, image='https://img/p1.jpg')]],
        track_pages={'p1': [[track_item('t1', 'One', artist='A')]]},
    )
    manager = SpotifyLibraryManager(client, cache_path)
    manager.refresh_all()

    loaded = SpotifyLibraryManager(FakeSpotifyWebAPI(), cache_path)

    assert [playlist.name for playlist in loaded.playlists] == ['Morning']
    assert loaded.playlists[0].image == 'https://img/p1.jpg'
    assert [track.name for track in loaded.tracks_for_playlist('p1')] == ['One']
    assert loaded.tracks_for_playlist('p1')[0].artist == 'A'
    assert not (tmp_path / 'spotify-library.json.tmp').exists()


def test_empty_playlist_caches_empty_track_list(tmp_path):
    client = FakeSpotifyWebAPI(
        playlist_pages=[[playlist_payload('empty', 'Empty', total=0)]],
        track_pages={'empty': [[]]},
    )
    manager = SpotifyLibraryManager(client, tmp_path / 'spotify-library.json')

    manager.refresh_all()

    assert [playlist.id for playlist in manager.playlists] == ['empty']
    assert manager.tracks_for_playlist('empty') == []


def test_missing_track_rows_are_skipped_without_breaking_order(tmp_path):
    client = FakeSpotifyWebAPI(
        track_pages={
            'p1': [[
                {'track': None},
                track_item('t1', 'One'),
                {'track': {'type': 'episode', 'name': 'Not a song', 'uri': 'spotify:episode:x'}},
                track_item('t2', 'Two'),
            ]],
        }
    )
    manager = SpotifyLibraryManager(client, tmp_path / 'spotify-library.json')

    tracks = manager.refresh_playlist_tracks('p1')

    assert [track.name for track in tracks] == ['One', 'Two']
    assert [track.position for track in tracks] == [1, 3]


def test_playlist_items_new_shape_uses_item_field(tmp_path):
    new_shape = track_item('t1', 'One')
    new_shape['item'] = new_shape.pop('track')
    client = FakeSpotifyWebAPI(track_pages={'p1': [[new_shape]]})
    manager = SpotifyLibraryManager(client, tmp_path / 'spotify-library.json')

    tracks = manager.refresh_playlist_tracks('p1')

    assert [track.name for track in tracks] == ['One']
    assert [track.uri for track in tracks] == ['spotify:track:t1']


def test_unavailable_and_local_tracks_are_flagged_gracefully(tmp_path):
    unavailable = track_item('blocked', 'Blocked', playable=False)
    unavailable['track']['restrictions'] = {'reason': 'market'}
    local = {
        'track': {
            'id': None,
            'uri': 'spotify:local:Kid:Car:Ride:120',
            'type': 'track',
            'name': 'Car Ride',
            'artists': [{'name': 'Kid'}],
            'album': {'name': 'Local Files', 'images': []},
            'duration_ms': 120000,
            'is_local': True,
        },
    }
    client = FakeSpotifyWebAPI(track_pages={'p1': [[unavailable, local]]})
    manager = SpotifyLibraryManager(client, tmp_path / 'spotify-library.json')

    tracks = manager.refresh_playlist_tracks('p1')

    assert tracks[0].is_playable is False
    assert tracks[0].unavailable_reason == 'market'
    assert tracks[1].is_local is True
    assert tracks[1].is_playable is False
    assert tracks[1].unavailable_reason == 'local'

"""
Spotify library/cache manager.

Builds typed playlist and track data from SpotifyWebAPI and persists it to a
local JSON cache so the app can start with the last known library state.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from ..models import SpotifyPlaylist, SpotifyPlaylistTrack

logger = logging.getLogger(__name__)


class SpotifyLibraryClient(Protocol):
    """Client shape needed by SpotifyLibraryManager."""

    def current_user_playlists(self, limit: int = 50) -> List[dict]:
        ...

    def playlist_items(self, playlist_id: str, limit: int = 100) -> List[dict]:
        ...


class SpotifyLibraryManager:
    """Fetch and cache Spotify playlists and playlist tracks."""

    def __init__(
        self,
        client: SpotifyLibraryClient,
        cache_path: Path,
        autoload: bool = True,
    ):
        self.client = client
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._playlists: List[SpotifyPlaylist] = []
        self._tracks_by_playlist: Dict[str, List[SpotifyPlaylistTrack]] = {}
        self.updated_at: Optional[float] = None

        if autoload:
            self.load_cache()

    @property
    def playlists(self) -> List[SpotifyPlaylist]:
        """Cached playlists in Spotify/API order."""
        return list(self._playlists)

    def tracks_for_playlist(self, playlist_id: str) -> List[SpotifyPlaylistTrack]:
        """Cached tracks for a playlist in Spotify/API order."""
        return list(self._tracks_by_playlist.get(playlist_id, []))

    def load_cache(self) -> bool:
        """Load cached playlists and tracks from disk."""
        with self._lock:
            try:
                if not self.cache_path.exists():
                    self._playlists = []
                    self._tracks_by_playlist = {}
                    self.updated_at = None
                    return False

                data = json.loads(self.cache_path.read_text())
                if not isinstance(data, dict):
                    return False

                playlists = [
                    playlist
                    for playlist in (
                        SpotifyPlaylist.from_dict(item)
                        for item in data.get('playlists', [])
                    )
                    if playlist is not None
                ]

                raw_tracks = data.get('tracks', {})
                tracks_by_playlist: Dict[str, List[SpotifyPlaylistTrack]] = {}
                if isinstance(raw_tracks, dict):
                    for playlist_id, items in raw_tracks.items():
                        if not isinstance(items, list):
                            continue
                        tracks_by_playlist[str(playlist_id)] = [
                            track
                            for track in (
                                SpotifyPlaylistTrack.from_dict(item)
                                for item in items
                            )
                            if track is not None
                        ]

                self._playlists = playlists
                self._tracks_by_playlist = tracks_by_playlist
                self.updated_at = data.get('updated_at')
                return True
            except json.JSONDecodeError as e:
                logger.warning(f'Invalid Spotify library cache JSON: {e}')
            except (IOError, OSError) as e:
                logger.warning(f'Could not read Spotify library cache: {e}')

            self._playlists = []
            self._tracks_by_playlist = {}
            self.updated_at = None
            return False

    def refresh_playlists(self, save: bool = True) -> List[SpotifyPlaylist]:
        """Fetch current user's playlists and update the cache."""
        raw_playlists = self.client.current_user_playlists()
        playlists = [
            playlist
            for playlist in (
                SpotifyPlaylist.from_api(item)
                for item in raw_playlists
            )
            if playlist is not None
        ]

        with self._lock:
            self._playlists = playlists
            if save:
                self._save_cache_locked()
        return list(playlists)

    def refresh_playlist_tracks(
        self,
        playlist_id: str,
        save: bool = True,
    ) -> List[SpotifyPlaylistTrack]:
        """Fetch tracks for one playlist and update the cache."""
        raw_items = self.client.playlist_items(playlist_id)
        tracks = [
            track
            for track in (
                SpotifyPlaylistTrack.from_api_item(item, position=index)
                for index, item in enumerate(raw_items)
            )
            if track is not None
        ]

        with self._lock:
            self._tracks_by_playlist[playlist_id] = tracks
            if save:
                self._save_cache_locked()
        return list(tracks)

    def refresh_all(self, save: bool = True) -> List[SpotifyPlaylist]:
        """Fetch playlists and tracks for every playlist."""
        playlists = self.refresh_playlists(save=False)
        for playlist in playlists:
            self.refresh_playlist_tracks(playlist.id, save=False)
        if save:
            with self._lock:
                self._save_cache_locked()
        return playlists

    def _cache_payload(self) -> dict:
        return {
            'version': 1,
            'updated_at': self.updated_at or time.time(),
            'playlists': [playlist.to_dict() for playlist in self._playlists],
            'tracks': {
                playlist_id: [track.to_dict() for track in tracks]
                for playlist_id, tracks in self._tracks_by_playlist.items()
            },
        }

    def _save_cache_locked(self):
        """Save cache atomically. Caller must hold _lock."""
        self.updated_at = time.time()
        payload = self._cache_payload()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.cache_path.with_suffix(self.cache_path.suffix + '.tmp')
        try:
            temp_path.write_text(json.dumps(payload, indent=2))
            os.replace(temp_path, self.cache_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

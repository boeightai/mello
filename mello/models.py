"""
Mello Data Models - Core data structures.
"""
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Literal


class MenuState(Enum):
    """Setup menu states (replaces 3 separate booleans)."""
    CLOSED = auto()
    MAIN = auto()
    WIFI_LIST = auto()
    WIFI_AP = auto()
    BT_LIST = auto()
    VOLUME_LEVELS = auto()


@dataclass
class LibrespotStatus:
    """Parsed response from the go-librespot /status endpoint."""
    playing: bool = False
    paused: bool = False
    stopped: bool = True
    volume: Optional[int] = None
    context_uri: Optional[str] = None
    track_name: Optional[str] = None
    track_artist: Optional[str] = None
    track_album: Optional[str] = None
    track_cover: Optional[str] = None
    track_uri: Optional[str] = None
    position: int = 0
    duration: int = 0
    repeat_context: bool = False

    @classmethod
    def from_dict(cls, data: dict, context_uri: Optional[str] = None) -> 'LibrespotStatus':
        """Parse raw API dict into a typed object."""
        track = data.get('track') or {}
        if not isinstance(track, dict):
            track = {}
        
        artist_names = track.get('artist_names', [])
        artist = ', '.join(artist_names) if artist_names else None
        
        raw_context_uri = data.get('context_uri') if isinstance(data, dict) else None
        resolved_context_uri = raw_context_uri or context_uri

        return cls(
            playing=not data.get('stopped', True) and not data.get('paused', False),
            paused=data.get('paused', False),
            stopped=data.get('stopped', True),
            volume=data.get('volume'),
            context_uri=resolved_context_uri,
            track_name=track.get('name'),
            track_artist=artist,
            track_album=track.get('album_name'),
            track_cover=track.get('album_cover_url'),
            track_uri=track.get('uri'),
            position=track.get('position', 0),
            duration=track.get('duration', 0),
            repeat_context=bool(data.get('repeat_context', False)),
        )


@dataclass
class CatalogItem:
    """Represents an album or playlist in the catalog."""
    id: str
    uri: str
    name: str
    type: str = 'album'
    artist: Optional[str] = None
    image: Optional[str] = None
    images: Optional[List[str]] = None  # For playlist composite covers
    current_track: Optional[dict] = None
    is_temp: bool = False


@dataclass
class SpotifyPlaylist:
    """Typed playlist data returned by the Spotify library cache."""
    id: str
    uri: str
    name: str
    track_count: int = 0
    image: Optional[str] = None
    owner_name: Optional[str] = None
    snapshot_id: Optional[str] = None
    description: Optional[str] = None
    public: Optional[bool] = None

    @classmethod
    def from_api(cls, data: dict) -> Optional['SpotifyPlaylist']:
        """Parse a Spotify Web API playlist payload."""
        if not isinstance(data, dict):
            return None

        playlist_id = str(data.get('id') or '')
        uri = str(data.get('uri') or (f'spotify:playlist:{playlist_id}' if playlist_id else ''))
        name = str(data.get('name') or '')
        if not playlist_id or not uri or not name:
            return None

        images = data.get('images') if isinstance(data.get('images'), list) else []
        image = next(
            (img.get('url') for img in images if isinstance(img, dict) and img.get('url')),
            None,
        )

        tracks = data.get('tracks') if isinstance(data.get('tracks'), dict) else {}
        try:
            track_count = int(tracks.get('total') or 0)
        except (TypeError, ValueError):
            track_count = 0

        owner = data.get('owner') if isinstance(data.get('owner'), dict) else {}

        return cls(
            id=playlist_id,
            uri=uri,
            name=name,
            track_count=track_count,
            image=image,
            owner_name=owner.get('display_name'),
            snapshot_id=data.get('snapshot_id'),
            description=data.get('description'),
            public=data.get('public') if isinstance(data.get('public'), bool) else None,
        )

    @classmethod
    def from_dict(cls, data: dict) -> Optional['SpotifyPlaylist']:
        """Parse cached playlist data."""
        if not isinstance(data, dict):
            return None
        playlist_id = str(data.get('id') or '')
        uri = str(data.get('uri') or '')
        name = str(data.get('name') or '')
        if not playlist_id or not uri or not name:
            return None
        try:
            track_count = int(data.get('track_count') or 0)
        except (TypeError, ValueError):
            track_count = 0
        return cls(
            id=playlist_id,
            uri=uri,
            name=name,
            track_count=track_count,
            image=data.get('image'),
            owner_name=data.get('owner_name'),
            snapshot_id=data.get('snapshot_id'),
            description=data.get('description'),
            public=data.get('public') if isinstance(data.get('public'), bool) else None,
        )

    def to_dict(self) -> dict:
        """Serialize playlist data for cache storage."""
        return {
            'id': self.id,
            'uri': self.uri,
            'name': self.name,
            'track_count': self.track_count,
            'image': self.image,
            'owner_name': self.owner_name,
            'snapshot_id': self.snapshot_id,
            'description': self.description,
            'public': self.public,
        }


@dataclass
class SpotifyPlaylistTrack:
    """Typed playlist track data returned by the Spotify library cache."""
    id: Optional[str]
    uri: str
    name: str
    artists: List[str] = field(default_factory=list)
    album: Optional[str] = None
    duration_ms: int = 0
    image: Optional[str] = None
    position: int = 0
    added_at: Optional[str] = None
    added_by: Optional[str] = None
    is_local: bool = False
    is_playable: bool = True
    unavailable_reason: Optional[str] = None

    @property
    def artist(self) -> Optional[str]:
        """Artist names joined for UI display."""
        return ', '.join(self.artists) if self.artists else None

    @classmethod
    def from_api_item(cls, item: dict, position: int = 0) -> Optional['SpotifyPlaylistTrack']:
        """Parse a Spotify Web API playlist item row."""
        if not isinstance(item, dict):
            return None

        track = item.get('track')
        if not isinstance(track, dict):
            return None
        if track.get('type') and track.get('type') != 'track':
            return None

        is_local = bool(track.get('is_local'))
        uri = str(track.get('uri') or '')
        name = str(track.get('name') or '')
        if not uri or not name:
            return None

        artists_data = track.get('artists') if isinstance(track.get('artists'), list) else []
        artists = [
            str(artist.get('name'))
            for artist in artists_data
            if isinstance(artist, dict) and artist.get('name')
        ]

        album_data = track.get('album') if isinstance(track.get('album'), dict) else {}
        images = album_data.get('images') if isinstance(album_data.get('images'), list) else []
        image = next(
            (img.get('url') for img in images if isinstance(img, dict) and img.get('url')),
            None,
        )

        try:
            duration_ms = int(track.get('duration_ms') or 0)
        except (TypeError, ValueError):
            duration_ms = 0

        restrictions = track.get('restrictions') if isinstance(track.get('restrictions'), dict) else {}
        is_playable = track.get('is_playable')
        if is_playable is None:
            is_playable = not is_local and not restrictions
        is_playable = bool(is_playable) and not is_local

        unavailable_reason = None
        if is_local:
            unavailable_reason = 'local'
        elif not is_playable:
            unavailable_reason = restrictions.get('reason') or 'unavailable'

        added_by = item.get('added_by') if isinstance(item.get('added_by'), dict) else {}

        return cls(
            id=track.get('id'),
            uri=uri,
            name=name,
            artists=artists,
            album=album_data.get('name'),
            duration_ms=duration_ms,
            image=image,
            position=position,
            added_at=item.get('added_at'),
            added_by=added_by.get('id'),
            is_local=is_local,
            is_playable=is_playable,
            unavailable_reason=unavailable_reason,
        )

    @classmethod
    def from_dict(cls, data: dict) -> Optional['SpotifyPlaylistTrack']:
        """Parse cached playlist track data."""
        if not isinstance(data, dict):
            return None
        uri = str(data.get('uri') or '')
        name = str(data.get('name') or '')
        if not uri or not name:
            return None
        artists = data.get('artists') if isinstance(data.get('artists'), list) else []
        try:
            duration_ms = int(data.get('duration_ms') or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        try:
            position = int(data.get('position') or 0)
        except (TypeError, ValueError):
            position = 0
        return cls(
            id=data.get('id'),
            uri=uri,
            name=name,
            artists=[str(artist) for artist in artists],
            album=data.get('album'),
            duration_ms=duration_ms,
            image=data.get('image'),
            position=position,
            added_at=data.get('added_at'),
            added_by=data.get('added_by'),
            is_local=bool(data.get('is_local', False)),
            is_playable=bool(data.get('is_playable', True)),
            unavailable_reason=data.get('unavailable_reason'),
        )

    def to_dict(self) -> dict:
        """Serialize track data for cache storage."""
        return {
            'id': self.id,
            'uri': self.uri,
            'name': self.name,
            'artists': list(self.artists),
            'album': self.album,
            'duration_ms': self.duration_ms,
            'image': self.image,
            'position': self.position,
            'added_at': self.added_at,
            'added_by': self.added_by,
            'is_local': self.is_local,
            'is_playable': self.is_playable,
            'unavailable_reason': self.unavailable_reason,
        }


@dataclass
class NowPlaying:
    """Current playback state from librespot."""
    playing: bool = False
    paused: bool = False
    stopped: bool = True
    context_uri: Optional[str] = None
    track_name: Optional[str] = None
    track_artist: Optional[str] = None
    track_album: Optional[str] = None
    track_cover: Optional[str] = None
    track_uri: Optional[str] = None
    position: int = 0
    duration: int = 0
    repeat_context: bool = False
    
    @property
    def progress(self) -> float:
        """Get playback progress as 0.0-1.0."""
        if self.duration <= 0:
            return 0.0
        return min(1.0, self.position / self.duration)
    
    def __repr__(self) -> str:
        state = 'playing' if self.playing else ('paused' if self.paused else 'stopped')
        track = self.track_name or '(none)'
        return f'NowPlaying({state}, {track}, {self.position // 1000}s/{self.duration // 1000}s)'


@dataclass
class PlayState:
    """
    Unified play/loading state for UI feedback.
    
    Replaces multiple separate variables:
    - _optimistic_playing
    - _is_loading / _should_show_loading  
    - _loading_start_time
    """
    pending_action: Optional[Literal['play', 'pause']] = None
    loading_since: Optional[float] = None
    
    # Delay before showing spinner (prevents flicker)
    SPINNER_DELAY = 0.2
    
    def set_pending(self, action: Literal['play', 'pause']):
        """Set a pending play/pause action."""
        self.pending_action = action
        if action == 'play':
            self.loading_since = time.time()
        else:
            self.loading_since = None
    
    def clear(self):
        """Clear pending state (real data received)."""
        self.pending_action = None
        self.loading_since = None
    
    def start_loading(self):
        """Start loading state (for navigation pause, play timer, etc.)."""
        if self.loading_since is None:
            self.loading_since = time.time()
    
    def stop_loading(self):
        """Stop loading state."""
        self.loading_since = None
    
    @property
    def is_loading(self) -> bool:
        """True if loading long enough to show spinner (200ms delay)."""
        if self.loading_since is None:
            return False
        return time.time() - self.loading_since > self.SPINNER_DELAY
    
    @property
    def should_show_loading(self) -> bool:
        """True if in any loading state (for play button icon)."""
        if self.pending_action == 'pause':
            return False
        return self.loading_since is not None

    @property
    def pause_intent_active(self) -> bool:
        """True when a user pause intent should dominate UI state."""
        return self.pending_action == 'pause'
    
    def display_playing(self, actual_playing: bool) -> bool:
        """What the UI should show for play/pause state."""
        if self.pause_intent_active:
            return False
        if self.pending_action == 'play' or self.should_show_loading:
            return True
        return actual_playing

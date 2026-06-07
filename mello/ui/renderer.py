"""
Renderer - All drawing/rendering logic for the Mello UI.
"""
import logging
import time
import math
from typing import Optional, List, Dict, Tuple

import pygame
import pygame.gfxdraw

from .helpers import draw_aa_circle
from .image_cache import ImageCache
from .context import RenderContext
from ..models import CatalogItem, MenuState, NowPlaying
from ..config import (
    SCREEN_WIDTH, SCREEN_HEIGHT, COLORS,
    COVER_SIZE, COVER_SIZE_SMALL, COVER_SPACING,
    TRACK_INFO_X, CAROUSEL_X, CONTROLS_X, CAROUSEL_CENTER_Y,
    BTN_SIZE, PLAY_BTN_SIZE, BTN_SPACING, PROGRESS_BAR_WIDTH,
    DEFAULT_VOLUME_LEVELS,
)

# Headphone button Y position — symmetric to volume button on the opposite side.
# Volume: center_y + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL//2 - BTN_SIZE//2 ≈ 1173
# Headphone: center_y - (COVER_SIZE + COVER_SPACING) - COVER_SIZE_SMALL//2 + BTN_SIZE//2 ≈ 107
_HEADPHONE_BTN_Y = CAROUSEL_CENTER_Y - (COVER_SIZE + COVER_SPACING) - COVER_SIZE_SMALL // 2 + BTN_SIZE // 2

GLOBAL_RAIL_W = 170
GLOBAL_RAIL_PADDING = 12
GLOBAL_RAIL_BUTTON = 92
GLOBAL_RAIL_PLAY_W = 170
GLOBAL_RAIL_CENTER_Y = SCREEN_HEIGHT // 2
GLOBAL_RAIL_GAP = 34

logger = logging.getLogger(__name__)


class Renderer:
    """Handles all drawing/rendering for Mello UI."""
    
    def __init__(self, screen: pygame.Surface, image_cache: ImageCache, icons: Dict[str, pygame.Surface]):
        self.screen = screen
        self.image_cache = image_cache
        self.icons = icons
        
        # Fonts
        self.font_large = pygame.font.Font(None, 42)
        self.font_medium = pygame.font.Font(None, 32)
        self.font_small = pygame.font.Font(None, 24)
        
        # Caches
        self._bg_cache: Optional[pygame.Surface] = None
        self._progress_cache: Dict[str, pygame.Surface] = {}
        self._text_cache: Dict[str, pygame.Surface] = {}
        self._last_track_key: Optional[Tuple[str, str]] = None
        self._spinner_cache: Dict[int, List[pygame.Surface]] = {}  # size -> list of frames
        self._spinner_overlay_cache: Dict[int, pygame.Surface] = {}  # size -> overlay
        self._spinner_frame_idx: int = 0  # Simple frame counter for consistent rotation
        
        # Partial update state
        self._needs_full_redraw = True
        self._static_layer: Optional[pygame.Surface] = None
        # Portrait mode: carousel spans Y axis (user's horizontal), X for vertical positioning
        self._carousel_rect = pygame.Rect(CAROUSEL_X - 50, 0, COVER_SIZE + 100, SCREEN_HEIGHT)
        self._last_playing_state: Optional[bool] = None
        self._last_selected_index: Optional[int] = None
        self._last_toast: Optional[str] = None
        
        # Button hit rectangles (updated during draw)
        self.add_button_rect: Optional[Tuple[int, int, int, int]] = None
        self.delete_button_rect: Optional[Tuple[int, int, int, int]] = None
        self.settings_button_rect: Optional[Tuple[int, int, int, int]] = None
        self.playlist_row_rects: List[Optional[pygame.Rect]] = []
        self.track_row_rects: List[Optional[pygame.Rect]] = []
        self.playlist_back_rect: Optional[pygame.Rect] = None
        self.playlist_settings_rect: Optional[pygame.Rect] = None
        self.track_back_rect: Optional[pygame.Rect] = None
        self.global_stop_play_rect: Optional[pygame.Rect] = None
        self.global_volume_down_rect: Optional[pygame.Rect] = None
        self.global_volume_up_rect: Optional[pygame.Rect] = None
        
        # Menu button rects (updated when menu is drawn)
        self.menu_button_rects: Dict[str, pygame.Rect] = {}
        self.menu_content_overflow: int = 0
        # Header fade: spans from content_top (transparent) to screen edge (opaque)
        # Covers the entire title zone so content slides under it smoothly (iOS-style)
        header_fade_size = SCREEN_WIDTH - 615  # 105px, starts just above first button top
        raw = self._build_fade_surface(header_fade_size)
        self._menu_header_fade = pygame.transform.flip(raw, True, False)

    def _clear_list_hit_rects(self):
        """Clear list-mode hit rectangles."""
        self.playlist_row_rects = []
        self.track_row_rects = []
        self.playlist_back_rect = None
        self.playlist_settings_rect = None
        self.track_back_rect = None
    
    @staticmethod
    def _build_fade_surface(size: int) -> pygame.Surface:
        """Create a black gradient: x=0 fully opaque, x=size fully transparent."""
        surf = pygame.Surface((size, SCREEN_HEIGHT), pygame.SRCALPHA)
        for i in range(size):
            alpha = 255 - int(255 * i / size)
            pygame.draw.line(surf, (0, 0, 0, alpha), (i, 0), (i, SCREEN_HEIGHT - 1))
        return surf

    def invalidate(self):
        """Force a full redraw on next frame."""
        self._needs_full_redraw = True
    
    @staticmethod
    def _get_track_key(item: Optional[CatalogItem], now_playing: NowPlaying,
                       is_loading: bool, pending_focus_uri: Optional[str],
                       requested_focus_uri: Optional[str], play_in_progress: bool) -> Optional[Tuple[str, str]]:
        """Return (name, artist) tuple for the title area, or None."""
        if not item:
            return None
        # Keep title tied to the focused context, and preserve it while paused
        # so resume shows the same track user paused on.
        if ((now_playing.playing or now_playing.paused) and
                now_playing.context_uri == item.uri and
                now_playing.track_name):
            return (now_playing.track_name, now_playing.track_artist or '')
        # Fallback: show last saved track metadata for this context while
        # live now_playing has not caught up yet.
        current_track = item.current_track if isinstance(item.current_track, dict) else None
        if current_track and current_track.get('name'):
            return (current_track.get('name'), current_track.get('artist') or '')
        return None
    
    def draw(self, ctx: RenderContext) -> Optional[List[pygame.Rect]]:
        """
        Main draw method.
        
        Args:
            ctx: RenderContext with all state needed to render
        
        Returns list of dirty rects for partial update, or None for full flip.
        """
        # Sleep mode - show black screen only
        if ctx.is_sleeping:
            self.screen.fill((0, 0, 0))
            self._needs_full_redraw = True
            return None
        
        # Menu overlay — draw full scene then overlay on top
        if ctx.menu_state != MenuState.CLOSED:
            self._clear_list_hit_rects()
            self.add_button_rect = None
            self.delete_button_rect = None
            self.settings_button_rect = None
            self._draw_menu_frame(ctx)
            return None

        # Clear button hit rects
        self._clear_list_hit_rects()
        self.add_button_rect = None
        self.delete_button_rect = None
        self.settings_button_rect = None
        
        current_item = ctx.items[ctx.selected_index] if ctx.selected_index < len(ctx.items) else None
        current_track_key = self._get_track_key(
            current_item,
            ctx.now_playing,
            ctx.is_loading,
            ctx.pending_focus_uri,
            ctx.requested_focus_uri,
            ctx.play_in_progress,
        )
        
        # Check if we need a full redraw
        state_changed = (
            self._last_playing_state != ctx.now_playing.playing or
            self._last_selected_index != ctx.selected_index or
            self._last_track_key is None or
            self._last_track_key != current_track_key
        )
        
        # Toast changes need redraw too
        toast_changed = self._last_toast != ctx.toast_message
        
        if state_changed:
            self._needs_full_redraw = True
            self._last_playing_state = ctx.now_playing.playing
            self._last_selected_index = ctx.selected_index
        
        # Empty state
        if not ctx.items:
            self._draw_background()
            self._draw_empty_state(ctx)
            self._needs_full_redraw = True
            return None
        
        # Calculate effective scroll position
        if ctx.dragging:
            drag_index_offset = -ctx.drag_offset / (COVER_SIZE + COVER_SPACING)
            effective_scroll = ctx.selected_index + drag_index_offset
        else:
            effective_scroll = ctx.scroll_x
        
        # Determine if animating
        is_animating = ctx.dragging or abs(ctx.scroll_x - ctx.selected_index) > 0.01
        
        if self._needs_full_redraw:
            # Full redraw
            self._draw_background()
            self._draw_track_info(current_item, ctx)
            self._draw_controls(ctx.is_playing, ctx.volume_index, ctx.pressed_button,
                                bt_connected=ctx.bt_connected, bt_audio_active=ctx.bt_audio_active)
            
            if self._static_layer is None:
                self._static_layer = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            self._static_layer.blit(self.screen, (0, 0))
            
            self._draw_carousel(ctx.items, effective_scroll, ctx.now_playing, ctx.delete_mode_id, ctx.is_loading)
            if ctx.toast_message:
                self._draw_toast(ctx.toast_message)
            self._last_toast = ctx.toast_message
            
            self._needs_full_redraw = False
            return None
        
        elif is_animating or toast_changed:
            # Partial update - only carousel area
            self.screen.blit(self._static_layer, 
                           self._carousel_rect.topleft, 
                           self._carousel_rect)
            self._draw_carousel(ctx.items, effective_scroll, ctx.now_playing, ctx.delete_mode_id, ctx.is_loading)
            if ctx.toast_message:
                self._draw_toast(ctx.toast_message)
            self._last_toast = ctx.toast_message
            return [self._carousel_rect]
        
        else:
            if ctx.now_playing.playing or ctx.is_loading:
                self.screen.blit(self._static_layer,
                               self._carousel_rect.topleft,
                               self._carousel_rect)
                self._draw_carousel(ctx.items, effective_scroll, ctx.now_playing, ctx.delete_mode_id, ctx.is_loading)
                return [self._carousel_rect]
            return []
    
    def _draw_background(self):
        """Draw a controlled full-screen background (portrait mode)."""
        if not self._bg_cache:
            self._bg_cache = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            for x in range(SCREEN_WIDTH):
                t = x / max(1, SCREEN_WIDTH - 1)
                color = self._blend_color(COLORS['bg_primary'], COLORS['bg_secondary'], t * 0.72)
                pygame.draw.line(self._bg_cache, color, (x, 0), (x, SCREEN_HEIGHT))

            pygame.draw.line(
                self._bg_cache,
                self._blend_color(COLORS['accent'], COLORS['bg_secondary'], 0.55),
                (GLOBAL_RAIL_W + 14, 0),
                (GLOBAL_RAIL_W + 14, SCREEN_HEIGHT),
                2,
            )
            self._bg_cache = self._bg_cache.convert()
        
        self.screen.blit(self._bg_cache, (0, 0))

    @staticmethod
    def _blend_color(a: tuple, b: tuple, amount: float) -> tuple:
        """Blend two RGB colors."""
        amount = max(0.0, min(1.0, amount))
        return (
            int(a[0] + (b[0] - a[0]) * amount),
            int(a[1] + (b[1] - a[1]) * amount),
            int(a[2] + (b[2] - a[2]) * amount),
        )

    def _render_text_rotated(self, text: str, font: pygame.font.Font, color: tuple) -> pygame.Surface:
        """Render text rotated 90° CW for portrait display mode."""
        text_surface = font.render(text, True, color)
        return pygame.transform.rotate(text_surface, -90)  # -90 = 90° CW

    def global_control_rects(self) -> Dict[str, pygame.Rect]:
        """Return always-on transport rail hit rectangles."""
        center_x = GLOBAL_RAIL_W // 2
        center_y = GLOBAL_RAIL_CENTER_Y
        play_rect = pygame.Rect(
            center_x - GLOBAL_RAIL_BUTTON // 2,
            center_y - GLOBAL_RAIL_PLAY_W // 2,
            GLOBAL_RAIL_BUTTON,
            GLOBAL_RAIL_PLAY_W,
        )
        volume_down_rect = pygame.Rect(
            center_x - GLOBAL_RAIL_BUTTON // 2,
            play_rect.top - GLOBAL_RAIL_GAP - GLOBAL_RAIL_BUTTON,
            GLOBAL_RAIL_BUTTON,
            GLOBAL_RAIL_BUTTON,
        )
        volume_up_rect = pygame.Rect(
            center_x - GLOBAL_RAIL_BUTTON // 2,
            play_rect.bottom + GLOBAL_RAIL_GAP,
            GLOBAL_RAIL_BUTTON,
            GLOBAL_RAIL_BUTTON,
        )
        return {
            'global_stop_play': play_rect,
            'global_volume_down': volume_down_rect,
            'global_volume_up': volume_up_rect,
        }

    def draw_global_transport(self, ctx: RenderContext):
        """Draw the non-dismissible STOP/PLAY and volume safety rail."""
        if ctx.is_sleeping:
            self.global_stop_play_rect = None
            self.global_volume_down_rect = None
            self.global_volume_up_rect = None
            return

        rail = pygame.Surface((GLOBAL_RAIL_W, SCREEN_HEIGHT), pygame.SRCALPHA)
        rail.fill((*COLORS['rail'], 248))
        pygame.draw.rect(
            rail,
            (*COLORS['accent_cool'], 22),
            (0, 0, GLOBAL_RAIL_W, SCREEN_HEIGHT // 2),
        )
        pygame.draw.rect(
            rail,
            (*COLORS['accent_warm'], 18),
            (0, SCREEN_HEIGHT // 2, GLOBAL_RAIL_W, SCREEN_HEIGHT // 2),
        )
        self.screen.blit(rail, (0, 0))
        pygame.draw.line(
            self.screen,
            COLORS['rail_edge'],
            (GLOBAL_RAIL_W - 1, 0),
            (GLOBAL_RAIL_W - 1, SCREEN_HEIGHT),
            2,
        )

        rects = self.global_control_rects()
        self.global_stop_play_rect = rects['global_stop_play']
        self.global_volume_down_rect = rects['global_volume_down']
        self.global_volume_up_rect = rects['global_volume_up']

        playing = ctx.is_playing and not ctx.hard_stopped
        label = 'STOP' if playing else 'PLAY'
        main_color = COLORS['stop'] if playing else COLORS['accent']
        pressed = ctx.global_pressed_button

        self._draw_global_button(
            self.global_volume_down_rect,
            'VOL -',
            COLORS['surface'],
            pressed == 'global_volume_down',
        )
        self._draw_global_button(
            self.global_stop_play_rect,
            label,
            main_color,
            pressed == 'global_stop_play',
            font=self.font_large,
        )
        self._draw_global_button(
            self.global_volume_up_rect,
            'VOL +',
            COLORS['surface'],
            pressed == 'global_volume_up',
        )

    def _draw_global_button(
        self,
        rect: pygame.Rect,
        label: str,
        bg_color: tuple,
        pressed: bool,
        font: Optional[pygame.font.Font] = None,
    ):
        """Draw one large family-safe global rail button."""
        color = self._lighten_color(bg_color) if pressed else bg_color
        shadow = pygame.Surface((rect.width + 10, rect.height + 10), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 70), shadow.get_rect(), border_radius=16)
        self.screen.blit(shadow, (rect.x - 2, rect.y + 5))
        pygame.draw.rect(self.screen, color, rect, border_radius=14)
        edge = self._blend_color(color, COLORS['text_primary'], 0.45)
        pygame.draw.rect(self.screen, edge, rect, width=2, border_radius=14)
        text = self._render_text_rotated(label, font or self.font_medium, COLORS['text_primary'])
        self.screen.blit(text, text.get_rect(center=rect.center))
    
    def _draw_empty_state(self, ctx: RenderContext):
        """Draw idle screen when catalog is empty (portrait mode)."""
        center_x = SCREEN_WIDTH // 2
        center_y = SCREEN_HEIGHT // 2
        panel = pygame.Surface((260, 720), pygame.SRCALPHA)
        pygame.draw.rect(panel, (*COLORS['surface'], 155), panel.get_rect(), border_radius=28)
        pygame.draw.rect(panel, (*COLORS['accent'], 70), (0, 0, 12, 720), border_radius=12)
        self.screen.blit(panel, (center_x - 40, center_y - 360))

        # Logo (scaled to ~160px wide, rotated to match landscape orientation)
        logo = self.icons.get('logo')
        if logo:
            logo_width = 320
            scale = logo_width / logo.get_width()
            logo_scaled = pygame.transform.smoothscale(
                logo,
                (logo_width, int(logo.get_height() * scale)),
            )
            logo_rotated = pygame.transform.rotate(logo_scaled, -90)
            logo_rect = logo_rotated.get_rect(center=(center_x + 80, center_y))
            self.screen.blit(logo_rotated, logo_rect)

        if not ctx.has_network:
            # No internet: show message + tappable Settings button
            line1 = self._render_text_rotated('No internet connection', self.font_medium, COLORS['text_secondary'])
            line1_rect = line1.get_rect(center=(center_x - 30, center_y))
            self.screen.blit(line1, line1_rect)

            # Settings button with rounded-rect background
            btn_text = self._render_text_rotated('Settings', self.font_medium, COLORS['text_primary'])
            btn_text_rect = btn_text.get_rect(center=(center_x - 90, center_y))
            pad_x, pad_y = 14, 10
            btn_bg = pygame.Rect(
                btn_text_rect.x - pad_x,
                btn_text_rect.y - pad_y,
                btn_text_rect.width + pad_x * 2,
                btn_text_rect.height + pad_y * 2,
            )
            pygame.draw.rect(self.screen, COLORS['accent'], btn_bg, border_radius=12)
            self.screen.blit(btn_text, btn_text_rect)
            self.settings_button_rect = (btn_bg.x, btn_bg.y, btn_bg.width, btn_bg.height)
        else:
            # Normal empty state: Spotify instructions
            self.settings_button_rect = None
            line1 = self._render_text_rotated('Play to Mello via Spotify', self.font_medium, COLORS['text_secondary'])
            line1_rect = line1.get_rect(center=(center_x - 30, center_y))
            self.screen.blit(line1, line1_rect)

            line2 = self._render_text_rotated('Tap + to save', self.font_medium, COLORS['text_secondary'])
            line2_rect = line2.get_rect(center=(center_x - 60, center_y))
            self.screen.blit(line2, line2_rect)
    
    def _draw_track_info(self, item: Optional[CatalogItem], ctx: RenderContext):
        """Draw track name and artist (portrait mode - at user's top)."""
        if not item:
            return
        
        track_key = self._get_track_key(
            item,
            ctx.now_playing,
            ctx.is_loading,
            ctx.pending_focus_uri,
            ctx.requested_focus_uri,
            ctx.play_in_progress,
        )
        if not track_key:
            return
        name, artist = track_key
        if track_key != self._last_track_key:
            self._last_track_key = track_key
            
            # Portrait mode: max_width is along Y axis (user's horizontal)
            max_width = SCREEN_HEIGHT - 100
            display_name = name
            
            # First render unrotated to check width
            name_surface = self.font_large.render(display_name, True, COLORS['text_primary'])
            if name_surface.get_width() > max_width:
                # Re-render every truncation step; otherwise width never changes
                # and we collapse almost every long title to 3 chars + ellipsis.
                while len(display_name) > 3:
                    trial_text = display_name + '...'
                    trial_surface = self.font_large.render(trial_text, True, COLORS['text_primary'])
                    if trial_surface.get_width() <= max_width - 30:
                        name_surface = trial_surface
                        break
                    display_name = display_name[:-1]
                else:
                    name_surface = self.font_large.render(display_name + '...', True, COLORS['text_primary'])
            
            # Now rotate for portrait display
            name_surface = pygame.transform.rotate(name_surface, -90)
            self._text_cache['name_surface'] = name_surface
            # Position: X=TRACK_INFO_X (user's top), Y centered
            self._text_cache['name_rect'] = name_surface.get_rect(center=(TRACK_INFO_X, CAROUSEL_CENTER_Y))
            
            if artist:
                artist_surface = self._render_text_rotated(artist, self.font_medium, COLORS['text_secondary'])
                self._text_cache['artist_surface'] = artist_surface
                # Artist below title in user's view = lower X value
                self._text_cache['artist_rect'] = artist_surface.get_rect(center=(TRACK_INFO_X - 35, CAROUSEL_CENTER_Y))
            else:
                self._text_cache['artist_surface'] = None
        
        self.screen.blit(self._text_cache['name_surface'], self._text_cache['name_rect'])
        if self._text_cache.get('artist_surface'):
            self.screen.blit(self._text_cache['artist_surface'], self._text_cache['artist_rect'])
    
    def _draw_carousel(self, items: List[CatalogItem], scroll_x: float, 
                       now_playing: NowPlaying, delete_mode_id: Optional[str], loading: bool = False):
        """Draw album cover carousel (portrait mode - covers along Y axis)."""
        # Portrait mode: covers laid out along Y axis (user's horizontal)
        center_y = CAROUSEL_CENTER_Y  # 640
        x = CAROUSEL_X  # Vertical position for covers
        
        max_index = max(0, len(items) - 1)
        scroll_x = max(0, min(scroll_x, max_index))
        
        start_i = max(0, int(scroll_x) - 2)
        end_i = min(len(items), int(scroll_x) + 3)
        
        center_cover_rect = None
        center_item = None
        # Draw covers
        for i in range(start_i, end_i):
            item = items[i]
            offset = i - scroll_x
            # Y position based on scroll (along user's horizontal)
            y = center_y + offset * (COVER_SIZE + COVER_SPACING)
            
            is_center = abs(offset) < 0.5
            size = COVER_SIZE if is_center else COVER_SIZE_SMALL
            
            draw_y = int(y - size // 2)
            # X position: center vertically, with smaller covers slightly offset
            draw_x = x + (COVER_SIZE - size) // 2
            
            if draw_y + size < 0 or draw_y > SCREEN_HEIGHT:
                continue
            
            # All items (albums and playlists) use single image field
            # Composites for playlists are pre-rendered and stored as single image
            if is_center:
                if loading:
                    cover = self.image_cache.get_dimmed(item.image, size)
                else:
                    cover = self.image_cache.get(item.image, size)
                center_cover_rect = (draw_x, draw_y, size, size)
                center_item = item
            else:
                cover = self.image_cache.get_dimmed(item.image, size)
            
            if is_center:
                shadow = pygame.Surface((size + 24, size + 24), pygame.SRCALPHA)
                pygame.draw.rect(shadow, (0, 0, 0, 105), shadow.get_rect(), border_radius=34)
                self.screen.blit(shadow, (draw_x - 8, draw_y + 10))
                pygame.draw.rect(
                    self.screen,
                    self._blend_color(COLORS['bg_elevated'], COLORS['accent'], 0.28),
                    (draw_x - 7, draw_y - 7, size + 14, size + 14),
                    border_radius=26,
                )
            self.screen.blit(cover, (draw_x, draw_y))
            if is_center:
                pygame.draw.rect(
                    self.screen,
                    self._blend_color(COLORS['text_primary'], COLORS['accent'], 0.08),
                    (draw_x, draw_y, size, size),
                    width=3,
                    border_radius=18,
                )
        
        if center_cover_rect and center_item:
            self._draw_cover_progress(center_cover_rect, center_item, now_playing)
            
            if loading:
                self._draw_loading_spinner(center_cover_rect)
            
            if center_item.is_temp:
                self._draw_add_button(center_cover_rect)
            elif delete_mode_id == center_item.id:
                self._draw_delete_button(center_cover_rect)
    
    def _draw_cover_progress(self, cover_rect: tuple, item: CatalogItem, now_playing: NowPlaying):
        """Draw progress bar at the edge of the cover (portrait mode - left edge = user's bottom)."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        if now_playing.context_uri != item.uri:
            return
        
        progress = now_playing.progress
        if progress <= 0:
            return
        
        # Portrait mode: progress bar on left edge of cover (user sees as bottom)
        bar_width = PROGRESS_BAR_WIDTH
        fill_height = int(cover_h * min(progress, 1.0))
        
        if fill_height <= 0:
            return
        
        # Cache progress bar mask
        mask_key = f'_progress_mask_{cover_w}'
        if mask_key not in self._progress_cache:
            radius = max(12, cover_w // 25)
            mask = pygame.Surface((cover_w, cover_h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, cover_w, cover_h), border_radius=radius)
            self._progress_cache[mask_key] = mask
        
        # Reuse cached progress surface
        surf_key = f'_progress_surf_{cover_w}'
        if surf_key not in self._progress_cache:
            self._progress_cache[surf_key] = pygame.Surface((cover_w, cover_h), pygame.SRCALPHA)
        
        progress_surf = self._progress_cache[surf_key]
        progress_surf.fill((0, 0, 0, 0))
        
        # Progress bar on left edge (user's bottom), growing from top to bottom
        # User sees this as progress from left to right
        pygame.draw.rect(progress_surf, COLORS['accent'],
                        (0, 0, bar_width, fill_height))
        
        progress_surf.blit(self._progress_cache[mask_key], (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self.screen.blit(progress_surf, (cover_x, cover_y))
    
    def _lighten_color(self, color: tuple, amount: float = 0.3) -> tuple:
        """Make a color lighter by blending with white."""
        r, g, b = color[:3]
        return (
            min(255, int(r + (255 - r) * amount)),
            min(255, int(g + (255 - g) * amount)),
            min(255, int(b + (255 - b) * amount)),
        )
    
    def _draw_controls(self, is_playing: bool, volume_index: int, pressed_button: Optional[str] = None,
                       bt_connected: bool = False, bt_audio_active: bool = False):
        """Draw playback control buttons (portrait mode - buttons along Y axis)."""
        x = CONTROLS_X
        center_y = CAROUSEL_CENTER_Y
        btn_spacing = BTN_SPACING

        gray_color = COLORS['surface']
        play_color = COLORS['accent']

        # Headphone button — only when BT is connected, opposite corner from volume
        if bt_connected:
            hp_center = (x, _HEADPHONE_BTN_Y)
            hp_color = COLORS['accent'] if bt_audio_active else gray_color
            if pressed_button == 'headphone':
                hp_color = self._lighten_color(hp_color)
            draw_aa_circle(self.screen, hp_color, hp_center, BTN_SIZE // 2)
            self._draw_icon('headphone', hp_center)

        # Prev button
        prev_center = (x, center_y - btn_spacing)
        prev_color = self._lighten_color(gray_color) if pressed_button == 'prev' else gray_color
        draw_aa_circle(self.screen, prev_color, prev_center, BTN_SIZE // 2)
        self._draw_icon('prev', prev_center)

        # Play/Pause button
        play_center = (x, center_y)
        play_btn_color = self._lighten_color(play_color) if pressed_button == 'play' else play_color
        draw_aa_circle(self.screen, play_btn_color, play_center, PLAY_BTN_SIZE // 2)
        self._draw_icon('pause' if is_playing else 'play', play_center)

        # Next button
        next_center = (x, center_y + btn_spacing)
        next_color = self._lighten_color(gray_color) if pressed_button == 'next' else gray_color
        draw_aa_circle(self.screen, next_color, next_center, BTN_SIZE // 2)
        self._draw_icon('next', next_center)

        # Volume button
        right_cover_edge = center_y + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_center = (x, right_cover_edge - BTN_SIZE // 2)
        vol_color = self._lighten_color(gray_color) if pressed_button == 'volume' else gray_color
        draw_aa_circle(self.screen, vol_color, vol_center, BTN_SIZE // 2)
        icon_key = DEFAULT_VOLUME_LEVELS[volume_index]['icon']
        self._draw_icon(icon_key, vol_center)
    
    def _draw_icon(self, name: str, center: tuple):
        """Draw an icon centered at position."""
        icon = self.icons.get(name)
        if icon:
            rect = icon.get_rect(center=center)
            self.screen.blit(icon, rect)
    
    def _draw_overlay_button(self, cover_rect: tuple, icon_name: str, tint: tuple) -> tuple:
        """Draw a tinted icon button on the cover. Returns (x, y, w, h) hit rect."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        btn_size, icon_size, margin = 100, 72, 16
        touch_padding = 60
        circle_radius = int(icon_size * (42 / 56) / 2)
        btn_x = cover_x + margin
        btn_y = cover_y + cover_h - btn_size - margin
        center = (btn_x + btn_size // 2, btn_y + btn_size // 2)
        
        draw_aa_circle(self.screen, (255, 255, 255), center, circle_radius)
        
        icon = self.icons.get(icon_name)
        if icon:
            scaled = pygame.transform.smoothscale(icon, (icon_size, icon_size))
            tinted = scaled.copy()
            tinted.fill(tint, special_flags=pygame.BLEND_RGB_MULT)
            self.screen.blit(tinted, tinted.get_rect(center=center))
        
        hit_x = btn_x - touch_padding
        hit_y = btn_y - touch_padding
        hit_size = btn_size + touch_padding * 2
        return (hit_x, hit_y, hit_size, hit_size)
    
    def _draw_add_button(self, cover_rect: tuple):
        """Draw + button on cover for temp items."""
        self.add_button_rect = self._draw_overlay_button(cover_rect, 'plus', COLORS['accent'])
    
    def _draw_delete_button(self, cover_rect: tuple):
        """Draw - button on cover for delete mode."""
        self.delete_button_rect = self._draw_overlay_button(cover_rect, 'minus', COLORS['error'])
    
    def _generate_spinner_frames(self, size: int, num_frames: int = 30) -> List[pygame.Surface]:
        """Generate pre-rendered spinner frames for smooth animation with ease-in-out."""
        frames = []
        spinner_size = size * 0.15 * 1.25  # 15% of cover size, 25% larger
        dot_radius = max(2, int(spinner_size * 0.15 * 1.25 * 0.9))  # 25% larger, then 10% smaller
        dot_distance = spinner_size * 0.45 * 1.25  # 25% larger
        
        def ease_in_out(t: float) -> float:
            """Stronger cubic ease-in-out function for more pronounced acceleration/deceleration."""
            if t < 0.5:
                return 4.0 * t * t * t
            else:
                return 1.0 - pow(-2.0 * t + 2.0, 3) / 2.0
        
        for frame_idx in range(num_frames):
            # Create frame surface
            frame = pygame.Surface((size, size), pygame.SRCALPHA)
            center_x = size // 2
            center_y = size // 2
            
            # Split rotation into two 180-degree halves, each with ease-in-out
            half_frames = num_frames / 2.0
            if frame_idx < half_frames:
                # First half: 0 to 180 degrees with ease-in-out
                t = frame_idx / half_frames if half_frames > 0 else 0.0
                eased_t = ease_in_out(t)
                rotation_rad = math.radians(eased_t * 180)
            else:
                # Second half: 180 to 360 degrees with ease-in-out
                t = (frame_idx - half_frames) / half_frames if half_frames > 0 else 0.0
                eased_t = ease_in_out(t)
                rotation_rad = math.radians(180 + eased_t * 180)
            
            # Draw 4 solid white dots
            for i in range(4):
                corner_angle = rotation_rad + (i * math.pi / 2)
                dot_x = int(center_x + math.cos(corner_angle) * dot_distance)
                dot_y = int(center_y + math.sin(corner_angle) * dot_distance)
                pygame.draw.circle(frame, (255, 255, 255), (dot_x, dot_y), dot_radius)
            
            frames.append(frame.convert_alpha())
        
        return frames
    
    def _get_spinner_overlay(self, size: int) -> pygame.Surface:
        """Get or create cached dimming overlay for spinner."""
        if size not in self._spinner_overlay_cache:
            overlay = pygame.Surface((size, size), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 115))  # 45% dark overlay
            self._spinner_overlay_cache[size] = overlay.convert_alpha()
        return self._spinner_overlay_cache[size]
    
    def _draw_loading_spinner(self, cover_rect: tuple):
        """Draw loading spinner on cover art using pre-rendered frames.
        
        No overlay needed — the center cover is already drawn dimmed when loading.
        """
        cover_x, cover_y, cover_w, cover_h = cover_rect
        size = max(cover_w, cover_h)
        
        # Get or create cached spinner frames
        if size not in self._spinner_cache:
            self._spinner_cache[size] = self._generate_spinner_frames(size, num_frames=30)
        
        frames = self._spinner_cache[size]
        
        # Simple frame counter - increments every draw call
        # At 30 FPS, this gives 1 rotation per second
        # At 60 FPS, 2 rotations per second (still smooth)
        frame = frames[self._spinner_frame_idx % len(frames)]
        self._spinner_frame_idx += 1
        
        # Blit the pre-rendered frame
        self.screen.blit(frame, (cover_x, cover_y))
    
    def _draw_toast(self, message: str):
        """Draw a toast pill with rotated text, centered on the carousel area."""
        text_surface = self.font_medium.render(message, True, COLORS['text_primary'])
        text_w, text_h = text_surface.get_size()
        
        # Pill dimensions (padding around text, then rotated)
        pad_x, pad_y = 24, 14
        pill_w = text_w + pad_x * 2
        pill_h = text_h + pad_y * 2
        
        pill = pygame.Surface((pill_w, pill_h), pygame.SRCALPHA)
        pygame.draw.rect(pill, (30, 30, 30, 220), (0, 0, pill_w, pill_h), border_radius=pill_h // 2)
        pill.blit(text_surface, (pad_x, pad_y))
        
        # Rotate for portrait display
        rotated = pygame.transform.rotate(pill, -90)
        
        # Center on carousel area
        rect = rotated.get_rect(center=(CAROUSEL_X + COVER_SIZE // 2, CAROUSEL_CENTER_Y))
        self.screen.blit(rotated, rect)

    # ============================================
    # LIST MODE
    # ============================================

    # Physical portrait panel is rotated for the user. Rows stack downward in
    # user-space by moving toward smaller physical X values.
    _LIST_TITLE_X = 670
    _LIST_NAV_SIZE = 64
    _LIST_NAV_CENTER = (670, 50)
    _LIST_SETTINGS_CENTER = (670, 1230)
    _LIST_ROW_X = 560
    _LIST_ROW_Y = 80
    _LIST_ROW_H = 82
    _LIST_ROW_W = 1120
    _LIST_ROW_GAP = 10
    _LIST_ROW_RADIUS = 14
    _LIST_THUMB_SIZE = 58
    _LIST_TEXT_LEFT = 170
    _LIST_TEXT_RIGHT = 50

    def _truncate_text(self, text: Optional[str], font, max_width: int) -> str:
        """Trim text so its unrotated width fits after portrait rotation."""
        text = (text or '').strip()
        if not text:
            return ''
        if font.size(text)[0] <= max_width:
            return text

        suffix = '...'
        max_body_width = max(0, max_width - font.size(suffix)[0])
        trimmed = text
        while trimmed and font.size(trimmed)[0] > max_body_width:
            trimmed = trimmed[:-1].rstrip()
        return (trimmed or text[:1]) + suffix

    def _list_text_max_width(self) -> int:
        """Width available for one rotated text line inside a list row."""
        return self._LIST_ROW_W - self._LIST_TEXT_LEFT - self._LIST_TEXT_RIGHT

    def _draw_list_title(self, title: str):
        display = self._truncate_text(title, self.font_large, SCREEN_HEIGHT - 180)
        title_surf = self._render_text_rotated(display, self.font_large, COLORS['text_primary'])
        self.screen.blit(title_surf, title_surf.get_rect(center=(self._LIST_TITLE_X, CAROUSEL_CENTER_Y)))

    def _draw_list_nav_button(self, icon_name: str, center: tuple, pressed: bool = False) -> pygame.Rect:
        nav_r = self._LIST_NAV_SIZE // 2
        nav_color = self._lighten_color(COLORS['surface']) if pressed else COLORS['surface']
        draw_aa_circle(self.screen, nav_color, center, nav_r)
        icon = self.icons.get(icon_name)
        if icon:
            icon_sz = 34
            scaled = pygame.transform.smoothscale(icon, (icon_sz, icon_sz))
            self.screen.blit(scaled, scaled.get_rect(center=center))
        return pygame.Rect(center[0] - nav_r, center[1] - nav_r, self._LIST_NAV_SIZE, self._LIST_NAV_SIZE)

    def _draw_list_empty_state(self, message: str):
        empty_surf = self._render_text_rotated(message, self.font_medium, COLORS['text_secondary'])
        center = (self._LIST_ROW_X - self._LIST_ROW_H, CAROUSEL_CENTER_Y)
        self.screen.blit(empty_surf, empty_surf.get_rect(center=center))

    def _draw_list_thumbnail(self, rect: pygame.Rect, image: Optional[str]):
        thumb_rect = pygame.Rect(
            rect.centerx - self._LIST_THUMB_SIZE // 2,
            rect.y + 26,
            self._LIST_THUMB_SIZE,
            self._LIST_THUMB_SIZE,
        )
        if image:
            cover = self.image_cache.get(image, self._LIST_THUMB_SIZE)
            halo = pygame.Surface((self._LIST_THUMB_SIZE + 10, self._LIST_THUMB_SIZE + 10), pygame.SRCALPHA)
            pygame.draw.rect(halo, (*COLORS['accent_warm'], 40), halo.get_rect(), border_radius=13)
            self.screen.blit(halo, (thumb_rect.x - 5, thumb_rect.y - 5))
            self.screen.blit(cover, thumb_rect)
        else:
            pygame.draw.rect(self.screen, COLORS['bg_secondary'], thumb_rect, border_radius=10)
            pygame.draw.rect(self.screen, COLORS['accent_family'], thumb_rect.inflate(-18, -18), border_radius=8)

    def _draw_list_row(self, rect: pygame.Rect, title: str, subtitle: str = '',
                       image: Optional[str] = None, highlighted: bool = False,
                       pressed: bool = False):
        is_liked_songs = title.strip().lower() == 'liked songs'
        bg_color = COLORS['accent'] if highlighted else (
            self._blend_color(COLORS['surface_warm'], COLORS['accent_family'], 0.24)
            if is_liked_songs else COLORS['surface']
        )
        if pressed:
            bg_color = self._lighten_color(bg_color)
        shadow = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 70), shadow.get_rect(), border_radius=self._LIST_ROW_RADIUS)
        self.screen.blit(shadow, (rect.x - 3, rect.y + 5))
        pygame.draw.rect(self.screen, bg_color, rect, border_radius=self._LIST_ROW_RADIUS)
        stripe_color = COLORS['accent_warm'] if is_liked_songs else COLORS['accent_cool']
        if highlighted:
            stripe_color = COLORS['accent_warm']
        pygame.draw.rect(
            self.screen,
            stripe_color,
            (rect.x, rect.y, rect.width, 8),
            border_radius=self._LIST_ROW_RADIUS,
        )
        pygame.draw.rect(
            self.screen,
            self._blend_color(bg_color, COLORS['text_primary'], 0.18),
            rect,
            width=1,
            border_radius=self._LIST_ROW_RADIUS,
        )

        self._draw_list_thumbnail(rect, image)

        max_text_width = self._list_text_max_width()
        title_display = self._truncate_text(title, self.font_medium, max_text_width)
        title_surf = self._render_text_rotated(title_display, self.font_medium, COLORS['text_primary'])

        if subtitle:
            subtitle_display = self._truncate_text(subtitle, self.font_small, max_text_width)
            subtitle_surf = self._render_text_rotated(subtitle_display, self.font_small, COLORS['text_secondary'])
            self.screen.blit(title_surf, title_surf.get_rect(center=(rect.centerx + 14, rect.y + 640)))
            self.screen.blit(subtitle_surf, subtitle_surf.get_rect(center=(rect.centerx - 18, rect.y + 640)))
        else:
            self.screen.blit(title_surf, title_surf.get_rect(center=(rect.centerx, rect.y + 640)))

    def _visible_list_rows(self, count: int, scroll_offset: int = 0) -> List[Tuple[int, pygame.Rect]]:
        rects: List[Tuple[int, pygame.Rect]] = []
        row_step = self._LIST_ROW_H + self._LIST_ROW_GAP
        for i in range(count):
            x = self._LIST_ROW_X - i * row_step + scroll_offset
            rect = pygame.Rect(x, self._LIST_ROW_Y, self._LIST_ROW_H, self._LIST_ROW_W)
            if rect.right < GLOBAL_RAIL_W + GLOBAL_RAIL_PADDING or rect.left > self._LIST_ROW_X + self._LIST_ROW_H:
                continue
            rects.append((i, rect))
        return rects

    def _track_from_row(self, row: dict) -> Tuple[str, str, Optional[str], Optional[str]]:
        track = row.get('track') if isinstance(row.get('track'), dict) else row
        name = track.get('name') or row.get('name') or 'Unknown track'
        uri = track.get('uri') or row.get('uri')

        artist = track.get('artist') or row.get('artist') or ''
        artists = track.get('artists') or row.get('artists') or []
        if not artist and isinstance(artists, list):
            artist_names = [a.get('name') for a in artists if isinstance(a, dict) and a.get('name')]
            artist = ', '.join(artist_names)

        image = track.get('image') or row.get('image')
        album = track.get('album') if isinstance(track.get('album'), dict) else {}
        images = album.get('images') if isinstance(album, dict) else None
        if not image and isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                image = first.get('url')
            elif isinstance(first, str):
                image = first

        return name, artist, uri, image

    def draw_playlist_list(self, playlists: List[CatalogItem], current_uri: Optional[str] = None,
                           scroll_offset: int = 0, pressed_index: Optional[int] = None,
                           title: str = 'Playlists', show_back: bool = False,
                           show_settings: bool = False) -> None:
        """Draw playlist selection list and populate playlist hit rectangles."""
        self._draw_background()
        self._clear_list_hit_rects()
        self.add_button_rect = None
        self.delete_button_rect = None
        self.settings_button_rect = None

        self._draw_list_title(title)
        if show_back:
            self.playlist_back_rect = self._draw_list_nav_button('back', self._LIST_NAV_CENTER)
        if show_settings and self.icons.get('settings'):
            self.playlist_settings_rect = self._draw_list_nav_button(
                'settings',
                self._LIST_SETTINGS_CENTER,
                pressed=False,
            )

        if not playlists:
            self._draw_list_empty_state('No playlists')
            return None

        self.playlist_row_rects = [None] * len(playlists)

        for index, rect in self._visible_list_rows(len(playlists), scroll_offset):
            self.playlist_row_rects[index] = rect
            playlist = playlists[index]
            self._draw_list_row(
                rect,
                playlist.name,
                playlist.artist or '',
                playlist.image,
                highlighted=bool(current_uri and playlist.uri == current_uri),
                pressed=index == pressed_index,
            )
        return None

    def draw_track_list(self, playlist: Optional[CatalogItem], tracks: List[dict],
                        now_playing: NowPlaying, current_track_uri: Optional[str] = None,
                        scroll_offset: int = 0, pressed_index: Optional[int] = None,
                        title: Optional[str] = None) -> None:
        """Draw tracks for a playlist and populate track hit rectangles."""
        self._draw_background()
        self._clear_list_hit_rects()
        self.add_button_rect = None
        self.delete_button_rect = None
        self.settings_button_rect = None

        title_text = title or (playlist.name if playlist else 'Tracks')
        self._draw_list_title(title_text)
        self.track_back_rect = self._draw_list_nav_button('back', self._LIST_NAV_CENTER)

        if not tracks:
            self._draw_list_empty_state('No tracks')
            return None

        active_uri = current_track_uri or now_playing.track_uri
        self.track_row_rects = [None] * len(tracks)

        for index, rect in self._visible_list_rows(len(tracks), scroll_offset):
            self.track_row_rects[index] = rect
            name, artist, uri, image = self._track_from_row(tracks[index])
            self._draw_list_row(
                rect,
                name,
                artist,
                image,
                highlighted=bool(active_uri and uri == active_uri),
                pressed=index == pressed_index,
            )
        return None
    
    # ============================================
    # SETUP MENU
    # ============================================

    # Shared layout constants for all menu screens.
    # Physical portrait 720x1280; user holds left-side up.
    # High physical X = user's top. Buttons stack downward (decreasing X).
    _MENU_BTN_H = 80
    _MENU_BTN_GAP = 10
    _MENU_BTN_W = 400
    _MENU_BTN_Y = 440           # physical Y start (centered on 640)
    _MENU_TITLE_X = 670         # title (fixed top)
    _MENU_CONTENT_TOP = 530     # first button x (below gradient fade)
    _MENU_CONTENT_BOT = 20      # content extends to near screen edge
    _MENU_NAV_SIZE = 60          # close/back icon button diameter
    _MENU_NAV_CENTER = (670, 50)   # close/back icon button center (user's top-left)

    _VOL_LABELS = [
        ('speaker', ['Speaker low', 'Speaker mid', 'Speaker high']),
        ('bt', ['BT low', 'BT mid', 'BT high']),
    ]

    def _draw_menu_frame(self, ctx: 'RenderContext'):
        """Draw fully black background then the active menu screen."""
        self.screen.fill((0, 0, 0))
        self.menu_button_rects = {}

        # Determine title and content items per screen
        if ctx.menu_state == MenuState.MAIN:
            title = 'Settings'
            nav_icon = 'close'
            items = self._build_main_content(ctx)
        elif ctx.menu_state == MenuState.WIFI_LIST:
            title = 'WiFi'
            nav_icon = 'back'
            items = self._build_wifi_content(ctx)
        elif ctx.menu_state == MenuState.WIFI_AP:
            title = 'WiFi'
            nav_icon = 'back'
            items = [
                ('text', '1. Connect to WiFi'),
                ('text', '    network "Mello-Setup"'),
                ('spacer',),
                ('text', '2. Choose your WiFi network'),
                ('spacer',),
                ('text', '3. Enter the password'),
            ]
        elif ctx.menu_state == MenuState.BT_LIST:
            title = 'Bluetooth'
            nav_icon = 'back'
            items = self._build_bt_content(ctx)
        elif ctx.menu_state == MenuState.VOLUME_LEVELS:
            title = 'Volume'
            nav_icon = 'back'
            items = self._build_volume_content(ctx)
        else:
            return

        H = self._MENU_BTN_H

        # 1. Draw scrollable content first (chrome overlays on top)
        self._draw_menu_content(items, ctx.menu_scroll_offset)

        # 2. Header fade: full gradient from content area (transparent) to screen edge (opaque)
        #    Content scrolls visibly under the title, fading out — iOS-style.
        self.screen.blit(self._menu_header_fade, (615, 0))

        # 3. Draw chrome on top: title + nav button
        title_surf = self._render_text_rotated(title, self.font_large, COLORS['text_primary'])
        self.screen.blit(title_surf, title_surf.get_rect(center=(self._MENU_TITLE_X, CAROUSEL_CENTER_Y)))

        nav_center = self._MENU_NAV_CENTER
        nav_r = self._MENU_NAV_SIZE // 2
        nav_color = COLORS['bg_elevated']
        if ctx.pressed_button == 'menu_close':
            nav_color = self._lighten_color(nav_color)
        draw_aa_circle(self.screen, nav_color, nav_center, nav_r)
        nav_icon_img = self.icons.get(nav_icon)
        if nav_icon_img:
            icon_sz = 32
            scaled = pygame.transform.smoothscale(nav_icon_img, (icon_sz, icon_sz))
            self.screen.blit(scaled, scaled.get_rect(center=nav_center))
        self.menu_button_rects['close'] = pygame.Rect(
            nav_center[0] - nav_r, nav_center[1] - nav_r,
            self._MENU_NAV_SIZE, self._MENU_NAV_SIZE)

        self._needs_full_redraw = True

    def _build_main_content(self, ctx: 'RenderContext') -> list:
        items = [
            ('button', 'wifi', 'WiFi', COLORS['bg_elevated']),
            ('button', 'bluetooth', 'Bluetooth', COLORS['bg_elevated']),
            ('button', 'volume', 'Volume levels', COLORS['bg_elevated']),
            ('separator',),
            ('button', 'auto_pause', f'Auto-pause: {ctx.auto_pause_minutes} min', COLORS['bg_elevated']),
            ('button', 'progress_expiry', f'Remember: {ctx.progress_expiry_hours} hrs', COLORS['bg_elevated']),
            ('separator',),
        ]
        # Dynamic update button
        if ctx.update_running:
            items.append(('button', 'check_update', 'Updating...', COLORS['bg_elevated']))
        elif ctx.update_checking:
            items.append(('button', 'check_update', 'Checking...', COLORS['bg_elevated']))
        elif ctx.update_available:
            items.append(('button', 'check_update', 'Update now', COLORS['accent']))
        else:
            items.append(('button', 'check_update', 'Check for updates', COLORS['bg_elevated']))
        items += [
            ('separator',),
            ('button', 'reset', 'Confirm Reset?' if ctx.reset_confirm_pending else 'Reset', COLORS['error']),
        ]
        if ctx.app_version_label:
            items.append(('footer', f'Version: {ctx.app_version_label}'))
        return items

    def _build_wifi_content(self, ctx: 'RenderContext') -> list:
        items = []
        for i, ssid in enumerate(ctx.menu_known_networks):
            is_current = ssid == ctx.menu_current_network
            color = COLORS['accent'] if is_current else COLORS['bg_elevated']
            display = ssid if len(ssid) <= 20 else ssid[:18] + '..'
            items.append(('button', f'reconnect_{i}', display, color))
        items.append(('separator',))
        items.append(('button', 'new_network', '+ New network', COLORS['bg_elevated']))
        return items

    def _build_bt_content(self, ctx: 'RenderContext') -> list:
        items = []
        if ctx.bt_paired_devices:
            items.append(('header', 'Paired'))
            for i, dev in enumerate(ctx.bt_paired_devices):
                color = COLORS['accent'] if dev.connected else COLORS['bg_elevated']
                label = dev.name if len(dev.name) <= 22 else dev.name[:20] + '..'
                items.append(('button', f'bt_paired_{i}', label, color))
            items.append(('separator',))
        if ctx.bt_discovered_devices:
            items.append(('header', 'Found'))
            for i, dev in enumerate(ctx.bt_discovered_devices):
                if ctx.bt_pairing_mac == dev.mac:
                    label = 'Connecting...'
                    color = COLORS['accent']
                else:
                    label = dev.name if len(dev.name) <= 22 else dev.name[:20] + '..'
                    color = COLORS['bg_elevated']
                items.append(('button', f'bt_discovered_{i}', label, color))
        elif ctx.bt_scanning:
            items.append(('header', 'Searching...'))
            items.append(('placeholder',))
        return items

    def _build_volume_content(self, ctx: 'RenderContext') -> list:
        items = []
        levels = ctx.volume_levels
        for idx, (output_type, names) in enumerate(self._VOL_LABELS):
            if idx > 0:
                items.append(('separator',))
            for i, name in enumerate(names):
                val = levels[i][output_type] if i < len(levels) else 0
                items.append(('vol_row', i, output_type, name, val))
        return items

    def _draw_menu_content(self, items: list, scroll_offset: int = 0):
        """Draw content items in the scrollable zone between title and back button."""
        H, GAP, W, Y = self._MENU_BTN_H, self._MENU_BTN_GAP, self._MENU_BTN_W, self._MENU_BTN_Y
        content_top = self._MENU_CONTENT_TOP
        content_bot = self._MENU_CONTENT_BOT

        # First pass: calculate total content height
        total_height = 0
        for item in items:
            kind = item[0]
            if kind == 'button':
                total_height += H + GAP
            elif kind == 'separator':
                total_height += GAP
            elif kind == 'header':
                total_height += 30
            elif kind == 'text':
                total_height += 35
            elif kind == 'spacer':
                total_height += 15
            elif kind == 'vol_row':
                total_height += H + GAP
            elif kind == 'placeholder':
                total_height += H + GAP
            elif kind == 'footer':
                total_height += 30
        if total_height > 0:
            total_height -= GAP  # remove trailing gap

        available = content_top - content_bot
        self.menu_content_overflow = max(0, total_height - available)

        # Set clip rect to content zone (buttons extend H pixels right from their x)
        clip = pygame.Rect(content_bot, 0, SCREEN_WIDTH - content_bot, SCREEN_HEIGHT)
        self.screen.set_clip(clip)

        # Draw items with scroll offset applied
        x = content_top + scroll_offset

        btn_w_vol = 70  # +/- button width for volume rows
        label_w_vol = W - btn_w_vol * 2 - 10

        for item in items:
            kind = item[0]

            if kind == 'button':
                _, btn_id, label, color = item
                btn = pygame.Rect(x, Y, H, W)
                self._draw_menu_button(btn, label, color)
                self.menu_button_rects[btn_id] = btn
                x -= H + GAP

            elif kind == 'separator':
                x -= GAP

            elif kind == 'header':
                hdr = self._render_text_rotated(item[1], self.font_small, COLORS['text_muted'])
                self.screen.blit(hdr, hdr.get_rect(center=(x, CAROUSEL_CENTER_Y)))
                x -= 30

            elif kind == 'text':
                surf = self._render_text_rotated(item[1], self.font_medium, COLORS['text_secondary'])
                self.screen.blit(surf, surf.get_rect(center=(x, CAROUSEL_CENTER_Y)))
                x -= 35

            elif kind == 'spacer':
                x -= 15

            elif kind == 'vol_row':
                _, i, output_type, name, val = item
                minus_rect = pygame.Rect(x, Y, H, btn_w_vol)
                self._draw_menu_button(minus_rect, '−', COLORS['bg_elevated'])
                self.menu_button_rects[f'vol_minus_{i}_{output_type}'] = minus_rect

                label_rect = pygame.Rect(x, Y + btn_w_vol + 5, H, label_w_vol)
                pygame.draw.rect(self.screen, COLORS['bg_secondary'], label_rect, border_radius=18)
                label_text = f'{name}: {val}%'
                label_surf = self._render_text_rotated(label_text, self.font_medium, COLORS['text_primary'])
                self.screen.blit(label_surf, label_surf.get_rect(center=label_rect.center))

                plus_rect = pygame.Rect(x, Y + btn_w_vol + 5 + label_w_vol + 5, H, btn_w_vol)
                self._draw_menu_button(plus_rect, '+', COLORS['bg_elevated'])
                self.menu_button_rects[f'vol_plus_{i}_{output_type}'] = plus_rect

                x -= H + GAP

            elif kind == 'placeholder':
                x -= H + GAP

            elif kind == 'footer':
                surf = self._render_text_rotated(item[1], self.font_small, COLORS['text_muted'])
                self.screen.blit(surf, surf.get_rect(center=(x, CAROUSEL_CENTER_Y)))
                x -= 30

        # Remove clip
        self.screen.set_clip(None)

        # Filter out button rects that are outside the visible content zone
        to_remove = []
        for btn_id, rect in self.menu_button_rects.items():
            if btn_id == 'close':
                continue  # close button is outside content zone by design
            if rect.x + rect.width <= content_bot or rect.x >= content_top + H:
                to_remove.append(btn_id)
        for btn_id in to_remove:
            del self.menu_button_rects[btn_id]

    def _draw_menu_button(self, rect: pygame.Rect, label: str, bg_color: tuple,
                          text_color: Optional[tuple] = None):
        """Draw a rounded rectangle button with rotated label."""
        text_color = text_color or COLORS['text_primary']
        pygame.draw.rect(self.screen, bg_color, rect, border_radius=18)
        text_surf = self._render_text_rotated(label, self.font_medium, text_color)
        self.screen.blit(text_surf, text_surf.get_rect(center=rect.center))

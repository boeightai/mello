"""
Tests for setup menu system actions.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from mello.managers.setup_menu import SetupMenu
import mello.managers.setup_menu as setup_menu_module


def test_factory_reset_clears_spotify_web_token_and_cache(tmp_path, monkeypatch):
    catalog_path = tmp_path / 'catalog.json'
    settings_path = tmp_path / 'settings.json'
    token_path = tmp_path / 'spotify-token.json'
    cache_path = tmp_path / 'spotify-library.json'
    state_path = tmp_path / 'librespot-state.json'
    images_dir = tmp_path / 'images'
    for path in (catalog_path, settings_path, token_path, cache_path):
        path.write_text('{}')
    state_path.write_text(json.dumps({'credentials': {'username': 'bo', 'data': 'secret'}}))
    images_dir.mkdir()
    (images_dir / 'cover.png').write_text('cover')

    monkeypatch.setattr(setup_menu_module, 'CATALOG_PATH', catalog_path)
    monkeypatch.setattr(setup_menu_module, 'SETTINGS_PATH', settings_path)
    monkeypatch.setattr(setup_menu_module, 'SPOTIFY_TOKEN_PATH', token_path)
    monkeypatch.setattr(setup_menu_module, 'SPOTIFY_LIBRARY_CACHE_PATH', cache_path)
    monkeypatch.setattr(setup_menu_module, 'LIBRESPOT_STATE_PATH', state_path)
    monkeypatch.setattr(setup_menu_module, 'IMAGES_DIR', images_dir)
    monkeypatch.setattr(
        setup_menu_module.subprocess,
        'run',
        lambda *args, **kwargs: SimpleNamespace(stdout=''),
    )
    monkeypatch.setattr(
        setup_menu_module.threading,
        'Thread',
        lambda *args, **kwargs: SimpleNamespace(start=lambda: None),
    )

    catalog_manager = SimpleNamespace(clear_all_progress=MagicMock())
    menu = SetupMenu(
        catalog_manager=catalog_manager,
        settings=SimpleNamespace(),
        on_toast=MagicMock(),
        on_invalidate=MagicMock(),
        on_library_cleared=MagicMock(),
    )

    menu._factory_reset()

    assert not catalog_path.exists()
    assert not settings_path.exists()
    assert not token_path.exists()
    assert not cache_path.exists()
    assert not images_dir.exists()
    state = json.loads(state_path.read_text())
    assert state['credentials'] == {'username': '', 'data': None}
    catalog_manager.clear_all_progress.assert_called_once()

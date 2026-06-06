"""
Static checks for Pi deployment files.
"""
from pathlib import Path


ROOT = Path(__file__).parent.parent


def test_librespot_service_is_not_part_of_ui_service():
    service = (ROOT / 'pi/systemd/mello-librespot.service.template').read_text()

    assert 'PartOf=mello-native.service' not in service


def test_librespot_waits_for_network_online():
    service = (ROOT / 'pi/systemd/mello-librespot.service.template').read_text()

    assert 'After=network-online.target' in service
    assert 'Wants=network-online.target' in service
    assert 'After=network.target' not in service
    assert 'Wants=network.target' not in service


def test_native_service_stop_timeout_and_log_cleanup_are_pi_safe():
    service = (ROOT / 'pi/systemd/mello-native.service.template').read_text()

    assert 'TimeoutStopSec=15' in service
    assert '-mtime +7 -delete; find __HOME__/mello -maxdepth 1 -name "mello.log.*" -size +50M -delete' in service
    assert r'\( -mtime +7 -o -size +50M \)' not in service


def test_installer_uses_boeightai_repo():
    install = (ROOT / 'install.sh').read_text()

    assert 'raw.githubusercontent.com/boeightai/mello/main/install.sh' in install
    assert 'https://github.com/boeightai/mello.git' in install
    assert 'emieljanson/mello' not in install


def test_auto_update_uses_boeightai_repo_and_migrates_legacy_remote():
    updater = (ROOT / 'pi/auto-update.sh').read_text()

    assert 'REPO_URL="${MELLO_REPO_URL:-https://github.com/boeightai/mello.git}"' in updater
    assert 'REPO_PATH="boeightai/mello"' in updater
    assert 'LEGACY_REPO_PATH="emieljanson/mello"' in updater
    assert '[ "$origin_url" = "$REPO_URL" ]' in updater
    assert 'git remote set-url origin "$REPO_URL"' in updater
    assert 'git remote add origin "$REPO_URL"' in updater


def test_migration_014_registered_for_librespot_dependency_update():
    migrate = (ROOT / 'pi/migrate.sh').read_text()

    assert '_migrate_014()' in migrate
    assert 'run_migration "014" "Keep librespot independent of UI sleep/restarts"' in migrate

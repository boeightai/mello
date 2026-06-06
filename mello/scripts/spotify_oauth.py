"""
Spotify OAuth setup helper for Mello.

Usage:
    python -m mello.scripts.spotify_oauth auth-url
    python -m mello.scripts.spotify_oauth exchange --redirect-url "http://127.0.0.1:8765/callback?code=..."
    python -m mello.scripts.spotify_oauth refresh

Set SPOTIFY_CLIENT_ID in .env or the shell before running.
"""
import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import (
    DATA_DIR,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_TOKEN_PATH,
)
from ..api.spotify_web import (
    DEFAULT_SPOTIFY_SCOPES,
    SpotifyWebAPI,
    SpotifyWebAPIError,
    build_authorize_url,
    exchange_authorization_code,
    generate_pkce_verifier,
    load_token_from_json,
    refresh_access_token,
    save_token_to_json,
)

PENDING_PATH = DATA_DIR / 'spotify-oauth-pending.json'


def _client_id(args) -> str:
    client_id = args.client_id or SPOTIFY_CLIENT_ID
    if not client_id:
        raise SystemExit('Set SPOTIFY_CLIENT_ID in .env or pass --client-id.')
    return client_id


def _scopes(args) -> list[str]:
    if args.scope:
        return args.scope
    return list(DEFAULT_SPOTIFY_SCOPES)


def _write_pending(payload: dict, path: Path = PENDING_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    path.chmod(0o600)


def _read_pending(path: Path = PENDING_PATH) -> dict:
    if not path.exists():
        raise SystemExit('No pending OAuth setup found. Run auth-url first.')
    return json.loads(path.read_text())


def _code_and_state_from_args(args) -> tuple[str, str | None]:
    if args.redirect_url:
        parsed = urlparse(args.redirect_url)
        params = parse_qs(parsed.query)
        code = (params.get('code') or [None])[0]
        state = (params.get('state') or [None])[0]
    else:
        code = args.code
        state = args.state
    if not code:
        raise SystemExit('Pass --redirect-url or --code from the Spotify callback.')
    return code, state


def cmd_auth_url(args):
    client_id = _client_id(args)
    redirect_uri = args.redirect_uri
    code_verifier = generate_pkce_verifier()
    state = secrets.token_urlsafe(24)
    scopes = _scopes(args)
    _write_pending({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier,
        'state': state,
        'scope': ' '.join(scopes),
    })
    print(build_authorize_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        scopes=scopes,
        state=state,
    ))


def cmd_exchange(args):
    pending = _read_pending()
    code, state = _code_and_state_from_args(args)
    expected_state = pending.get('state')
    if expected_state and state != expected_state:
        raise SystemExit('Spotify callback state did not match the pending OAuth setup.')

    token = exchange_authorization_code(
        client_id=pending['client_id'],
        redirect_uri=pending['redirect_uri'],
        code=code,
        code_verifier=pending['code_verifier'],
    )
    save_token_to_json(token, args.token_path)
    PENDING_PATH.unlink(missing_ok=True)
    print(f'Saved Spotify token to {args.token_path}')


def cmd_refresh(args):
    token = load_token_from_json(args.token_path)
    if not token:
        raise SystemExit(f'No Spotify token found at {args.token_path}')
    refreshed = refresh_access_token(token, _client_id(args))
    save_token_to_json(refreshed, args.token_path)
    print(f'Refreshed Spotify token at {args.token_path}')


def cmd_verify(args):
    token = load_token_from_json(args.token_path)
    if not token:
        raise SystemExit(f'No Spotify token found at {args.token_path}')
    client = SpotifyWebAPI(
        token=token,
        token_path=args.token_path,
        token_refresher=lambda old: refresh_access_token(old, _client_id(args)),
    )
    playlists = client.current_user_playlists(limit=10)
    devices = client.available_devices()
    print(f'Playlists visible: {len(playlists)}')
    print(f'Connect devices visible: {len(devices)}')
    for device in devices:
        name = device.get('name') or 'unknown'
        active = 'active' if device.get('is_active') else 'idle'
        restricted = ' restricted' if device.get('is_restricted') else ''
        print(f'- {name}: {active}{restricted}')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Set up Spotify OAuth for Mello.')
    parser.add_argument('--client-id', default=os.environ.get('SPOTIFY_CLIENT_ID'))
    parser.add_argument('--redirect-uri', default=SPOTIFY_REDIRECT_URI)
    parser.add_argument('--token-path', type=Path, default=SPOTIFY_TOKEN_PATH)
    parser.add_argument('--scope', action='append')
    sub = parser.add_subparsers(dest='command', required=True)

    auth_url = sub.add_parser('auth-url', help='Create and print a Spotify authorization URL.')
    auth_url.set_defaults(func=cmd_auth_url)

    exchange = sub.add_parser('exchange', help='Exchange the callback code for data/spotify-token.json.')
    exchange.add_argument('--redirect-url', help='Full callback URL copied from the browser address bar.')
    exchange.add_argument('--code', help='Authorization code from the callback.')
    exchange.add_argument('--state', help='Callback state, required only when passing --code manually.')
    exchange.set_defaults(func=cmd_exchange)

    refresh = sub.add_parser('refresh', help='Refresh an existing token file.')
    refresh.set_defaults(func=cmd_refresh)

    verify = sub.add_parser('verify', help='Verify token can read playlists and devices.')
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SpotifyWebAPIError as e:
        raise SystemExit(str(e))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

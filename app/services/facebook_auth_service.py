"""Explicit local provisioning of a verified Facebook Page token.

Token exchange is separate from publishing. No automatic renewal, redirects,
proxy/netrc inheritance, raw error logging or platform uploads.
"""
import os
import re
import tempfile
import time
from pathlib import Path

import requests

REQUIRED_SCOPES = {'pages_show_list', 'pages_read_engagement', 'pages_manage_posts'}


class FacebookAuthError(RuntimeError):
    pass


def meta_error(response, stage='authentication'):
    """Use numerical diagnostics and fixed guidance; never reflect raw errors."""
    try:
        body = response.json()
    except (ValueError, TypeError):
        body = {}
    error = body.get('error') if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get('code') if type(error.get('code')) is int else None
    subcode = error.get('error_subcode') if type(error.get('error_subcode')) is int else None
    details = f'Facebook {stage} failed (HTTP {response.status_code}'
    if code is not None:
        details += f', Meta code {code}'
    if subcode is not None:
        details += f'/{subcode}'
    details += '). '
    if code == 1 and str(error.get('message', '')).casefold().startswith('error validating client secret'):
        details += 'Meta rejected FACEBOOK_APP_SECRET. Copy the App Secret from this app\'s Settings → Basic, rather than a Client Token or access token. '
    elif code == 190:
        details += 'The Facebook token expired or was invalidated. Provision a new Page token using scripts/facebook_auth.py, then restart the API. '
    elif code in {10, 200}:
        details += 'Check Page content-creation access and the granted pages_manage_posts/pages_read_engagement permissions. '
    else:
        details += 'Inspect the Meta app, Page access and credentials. '
    return FacebookAuthError(details + 'No automatic upload retry was sent.')


class FacebookAuthService:
    def __init__(self, *, page_id=None, app_id=None, app_secret=None, api_version=None, session=None):
        self.page_id = page_id if page_id is not None else os.getenv('FACEBOOK_PAGE_ID', '')
        self.app_id = app_id if app_id is not None else os.getenv('FACEBOOK_APP_ID', '')
        self.app_secret = app_secret if app_secret is not None else os.getenv('FACEBOOK_APP_SECRET', '')
        self.version = api_version or os.getenv('META_GRAPH_API_VERSION', 'v26.0')
        if not re.fullmatch(r'v\d+\.\d+', self.version) or not self.page_id.isdigit():
            raise FacebookAuthError('Set a numeric FACEBOOK_PAGE_ID and a valid META_GRAPH_API_VERSION')
        self.session = session or requests.Session()
        self.session.trust_env = False

    def get(self, path, token, params=None):
        stage = {'debug_token': 'token inspection', 'oauth/access_token': 'token exchange',
                 'me': 'Page identity check'}.get(path, 'Page token lookup')
        try:
            response = self.session.get(f'https://graph.facebook.com/{self.version}/{path}',
                headers={'Authorization': 'Bearer ' + token} if token else {}, params=params,
                timeout=(5, 30), allow_redirects=False)
        except requests.RequestException:
            raise FacebookAuthError('Meta authentication request could not complete. No token was saved and no upload was sent.') from None
        if response.status_code != 200:
            raise meta_error(response, stage) from None
        try:
            body = response.json()
        except ValueError:
            raise FacebookAuthError('Meta returned unreadable authentication data; no token was saved') from None
        if not isinstance(body, dict) or body.get('error'):
            raise meta_error(response, stage) from None
        return body

    def inspect(self, token, *, inspector=None):
        if not token:
            raise FacebookAuthError('No token is configured for inspection')
        # A valid developer user token can inspect itself. App credentials are
        # preferred for Page tokens and required for exchanging a user token.
        auth = inspector or (f'{self.app_id}|{self.app_secret}' if self.app_id and self.app_secret else token)
        data = self.get('debug_token', auth, {'input_token': token}).get('data')
        if not isinstance(data, dict):
            raise FacebookAuthError('Meta returned no token metadata')
        if data.get('is_valid') is not True:
            raise FacebookAuthError('Facebook token is invalid; complete Facebook Login before provisioning a Page token')
        return data

    def check(self, token):
        data = self.inspect(token)
        identity = self.get('me', token, {'fields': 'id'})
        return {'type': data.get('type'), 'is_valid': data.get('is_valid'),
                'acts_as_configured_page': str(identity.get('id')) == self.page_id,
                'expires_at': data.get('expires_at'), 'data_access_expires_at': data.get('data_access_expires_at'),
                'missing_permissions': sorted(REQUIRED_SCOPES - set(data.get('scopes') or []))}

    def validate_metadata(self, data, expected_type):
        if data.get('type') != expected_type or str(data.get('app_id')) != self.app_id:
            raise FacebookAuthError(f'Token must be a {expected_type} token issued by the configured FACEBOOK_APP_ID')
        missing = REQUIRED_SCOPES - set(data.get('scopes') or [])
        if missing:
            raise FacebookAuthError('Facebook Login must grant these permissions before provisioning: ' + ', '.join(sorted(missing)))
        expiry = data.get('expires_at')
        if type(expiry) not in (int, float) or expiry < 0 or (expiry and expiry <= time.time()):
            raise FacebookAuthError('Token expiry is missing or expired; complete Facebook Login again')
        data_expiry = data.get('data_access_expires_at')
        if type(data_expiry) in (int, float) and data_expiry and data_expiry <= time.time():
            raise FacebookAuthError('Facebook data access expired; complete Facebook Login again')

    def provision(self, user_token):
        if not self.app_id.isdigit() or not self.app_secret:
            raise FacebookAuthError('Configure FACEBOOK_APP_ID and FACEBOOK_APP_SECRET locally in .env before exchanging the user token')
        info = self.inspect(user_token)
        self.validate_metadata(info, 'USER')
        # The OAuth exchange authenticates with client_id/client_secret and the
        # fb_exchange_token parameter, not a second Bearer authorization.
        exchanged = self.get('oauth/access_token', None, {
            'grant_type': 'fb_exchange_token', 'client_id': self.app_id,
            'client_secret': self.app_secret, 'fb_exchange_token': user_token})
        long_token = exchanged.get('access_token')
        if not isinstance(long_token, str) or not long_token:
            raise FacebookAuthError('Meta returned no exchanged token; no configuration was changed')
        long_info = self.inspect(long_token)
        self.validate_metadata(long_info, 'USER')
        expiry = long_info['expires_at']
        if expiry and expiry < time.time() + 7 * 86400:
            raise FacebookAuthError('Meta did not issue a user token valid for at least seven days; no Page token was saved')
        page = self.get(self.page_id, long_token, {'fields': 'id,access_token'})
        page_token = page.get('access_token')
        if str(page.get('id')) != self.page_id or not isinstance(page_token, str) or not page_token:
            raise FacebookAuthError('Meta did not return a token for the configured Page; check Page selection and content-creation access')
        page_info = self.inspect(page_token)
        self.validate_metadata(page_info, 'PAGE')
        expiry = page_info['expires_at']
        if expiry and expiry < time.time() + 7 * 86400:
            raise FacebookAuthError('The derived Page token is still short-lived; no token was saved')
        if str(self.get('me', page_token, {'fields': 'id'}).get('id')) != self.page_id:
            raise FacebookAuthError('The derived token acts as a different Page; no token was saved')
        return page_token, {'type': 'PAGE', 'is_valid': True, 'acts_as_configured_page': True,
            'expires_at': page_info['expires_at'], 'data_access_expires_at': page_info.get('data_access_expires_at'),
            'missing_permissions': []}


def save_page_token(env_path, token):
    """Replace only this setting atomically; never print or back up secrets."""
    from dotenv import dotenv_values
    from dotenv.parser import parse_stream
    from io import StringIO
    path = Path(env_path).resolve()
    if not path.is_file() or not re.fullmatch(r'[A-Za-z0-9_|.\-]+', token or ''):
        raise FacebookAuthError('A local .env and a valid opaque Page token are required')
    original = path.read_text(encoding='utf-8')
    bindings = list(parse_stream(StringIO(original)))
    matches = [binding for binding in bindings if binding.key == 'FACEBOOK_PAGE_ACCESS_TOKEN']
    if len(matches) > 1 or any(binding.error for binding in bindings):
        raise FacebookAuthError('Resolve duplicate FACEBOOK_PAGE_ACCESS_TOKEN settings before saving')
    replacement = 'FACEBOOK_PAGE_ACCESS_TOKEN=' + token
    updated = ''
    for binding in bindings:
        content = binding.original.string
        if binding.key == 'FACEBOOK_PAGE_ACCESS_TOKEN':
            start = re.search(r'(?m)^[ \t]*(?:export\s+)?FACEBOOK_PAGE_ACCESS_TOKEN\s*=', content)
            if not start:
                raise FacebookAuthError('Cannot safely update this token definition; no file was changed')
            content = content[:start.start()] + replacement + ('\n' if content.endswith('\n') else '')
        updated += content
    if not matches:
        updated = original.rstrip('\n') + '\n' + replacement + '\n'
    if dotenv_values(stream=StringIO(updated)).get('FACEBOOK_PAGE_ACCESS_TOKEN') != token:
        raise FacebookAuthError('Cannot safely update this token definition; no file was changed')
    identifier, name = tempfile.mkstemp(prefix='.facebook-token-', dir=path.parent)
    try:
        with os.fdopen(identifier, 'w', encoding='utf-8') as handle:
            os.chmod(name, 0o600)
            handle.write(updated); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

"""Official Runway SDK adapter. Submit once, persist the task ID, poll separately.

The SDK's automatic retries are explicitly disabled, including for timeouts.
Signed output URLs stay in memory and downloads never carry API credentials.
"""
import base64
import ipaddress
import math
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import requests
from runwayml import APIConnectionError, APIError, APIStatusError, RunwayML

from app.services.gemini_video_service import VideoProviderError
from app.services.runway_models import capabilities, ratio, validate

TASK_ID = re.compile(r'^[A-Za-z0-9_-]{1,200}$')


class RunwayVideoService:
    def __init__(self, config, *, client=None, session=None):
        self.config = config
        self.client = client
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.clear()

    def sdk(self):
        if self.client is None:
            key = os.getenv('RUNWAYML_API_SECRET')
            if not key:
                raise VideoProviderError('RUNWAYML_API_SECRET is not configured')
            self.client = RunwayML(api_key=key, base_url='https://api.dev.runwayml.com',
                max_retries=0, timeout=60.0,
                http_client=httpx.Client(trust_env=False, follow_redirects=False, timeout=60.0))
        return self.client

    @staticmethod
    def call(action, *, paid=False):
        try:
            return action()
        except APIStatusError as error:
            code = error.status_code
            uncertain = paid and (code >= 500 or code in {408, 409})
            raise VideoProviderError(f'Runway API returned HTTP {code}. Check account access, credits and provider records; no automatic retry was sent.', uncertain=uncertain) from None
        except (APIConnectionError, httpx.TransportError):
            raise VideoProviderError('Runway connection failed. Inspect provider records before another paid request; no retry was sent.', uncertain=paid) from None
        except APIError:
            raise VideoProviderError('Runway returned an unreadable response. Inspect task and billing history; no automatic retry was sent.', uncertain=paid) from None

    def check_model(self):
        # Read-only account access check, never generate a test clip.
        def read_account():
            try:
                return self.sdk().organization.retrieve()
            except APIStatusError as error:
                if error.status_code in {403, 404}:
                    # Some API roles may generate without organization access.
                    # Keep the estimate/confirmation available, with no claimed
                    # balance or account-model verification.
                    return None
                raise
        account = self.call(read_account)
        if account is None:
            return {'available_credits': None, 'credit_notice': 'Runway did not permit credit lookup; balance and model access are unverified'}
        balance = account.credit_balance
        if type(balance) not in (int, float) or not math.isfinite(balance) or balance < 0:
            return {'available_credits': None}
        return {'available_credits': balance}

    @staticmethod
    def image(data):
        if len(data) > 2 * 1024 * 1024:
            raise VideoProviderError('Runway image exceeds the normalized 2MB limit')
        return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')

    def submit(self, prompt, *, aspect_ratio, references=(), previous_id=None,
               operation='generate', duration=None, start_image=None):
        validate(self.config, duration)
        if previous_id or operation != 'generate':
            raise VideoProviderError('Runway supports a newly authorized shot candidate; Gemini edit/extension operations are unavailable')
        info = capabilities(self.config)
        limit = 1000 if self.config.video_model.startswith('gen4') else 26000 if self.config.video_model == 'kling_4' else 2500
        if len(prompt) > limit:
            # The common SDK model limit is 1000 characters. Fail before billing
            # instead of silently dropping dialogue, identity or instructions.
            raise VideoProviderError(f'Runway shot prompt exceeds {limit} characters; shorten this shot or its character descriptions before generation')
        payload = dict(model=self.config.video_model, prompt_text=prompt,
                       duration=duration, ratio=ratio(self.config))
        if info['audio'] and not info.get('always_audio'):
            payload['audio'] = self.config.narration == 'model_audio'
        if references:
            if not info['character_references'] or (self.config.video_input_mode == 'text' and self.config.video_model != 'kling_4'):
                raise VideoProviderError('This model/input mode does not support character images')
            payload['references'] = [{'uri': self.image(data)} for data in references]
        if self.config.video_input_mode == 'image':
            if not start_image:
                raise VideoProviderError('Image-to-video requires a permission-confirmed start frame for every shot')
            payload['prompt_image'] = self.image(start_image)
            if self.config.video_model == 'kling2.5_turbo_pro':
                payload.pop('ratio')  # This endpoint preserves the start image ratio.
            create = self.sdk().image_to_video.create
        else:
            create = self.sdk().text_to_video.create
        created = self.call(lambda: create(**payload), paid=True)
        identifier = getattr(created, 'id', None)
        if not isinstance(identifier, str) or not TASK_ID.fullmatch(identifier):
            raise VideoProviderError('Runway submission returned no usable task ID. Inspect billing and task history; do not resubmit.', uncertain=True)
        return {'interaction_id': identifier, 'status': 'in_progress', 'usage': {}}

    def retrieve(self, identifier):
        if not isinstance(identifier, str) or not TASK_ID.fullmatch(identifier):
            raise VideoProviderError('Invalid Runway task ID')
        task = self.call(lambda: self.sdk().tasks.retrieve(identifier))
        data = task.model_dump(mode='json')
        if data.get('id') != identifier:
            raise VideoProviderError('Runway task response did not match the saved task ID')
        remote = data.get('status')
        state = {'PENDING': 'in_progress', 'THROTTLED': 'in_progress', 'RUNNING': 'in_progress',
                 'SUCCEEDED': 'completed', 'FAILED': 'failed', 'CANCELLED': 'cancelled'}.get(remote, 'unknown')
        usage = {}
        for field, target in [('cost', 'credits'), ('estimated_cost', 'estimated_credits')]:
            number = (data.get(field) or {}).get('credits')
            if type(number) in (int, float) and math.isfinite(number) and number >= 0:
                usage[target] = number
        error = None
        if state not in {'completed', 'in_progress'}:
            # Raw failure strings may echo prompts or credentials. Retain only
            # well-formed provider codes and fixed, actionable guidance.
            code = data.get('failure_code', '')
            code = code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_.]{0,79}', code) else 'TASK_FAILED'
            error = f'Runway {code}. Inspect this task in Runway, then explicitly quote a new candidate if needed. No retry was sent.'
        return {'interaction_id': identifier, 'status': state, 'usage': usage,
                'provider': 'runway', 'provider_error': error, 'output': data.get('output', [])}

    @staticmethod
    def validate_url(url):
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower()
            # Fail closed for a new CDN rather than fetching arbitrary URLs.
            trusted = any(host.endswith('.' + suffix) for suffix in ('cloudfront.net', 'runwayml.com', 'runwaycdn.com'))
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443} or not trusted:
                raise ValueError
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError
        except (ValueError, TypeError, OSError):
            raise VideoProviderError('Runway returned an unsupported or non-public media download host; inspect the saved task without regenerating') from None

    def download(self, snapshot, path):
        outputs = snapshot.get('output', [])
        if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], str):
            raise VideoProviderError('Runway completed without a downloadable video; retrieve the existing task again')
        url = outputs[0]
        self.validate_url(url)
        path = Path(path)
        partial = path.with_suffix('.part')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.session.get(url, headers={}, stream=True, timeout=(5, 60), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise VideoProviderError('Runway media download failed; retrieve the existing task for a fresh link, without regenerating')
                size = 0
                with partial.open('wb') as handle:
                    for chunk in response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > 100 * 1024 * 1024:
                            raise VideoProviderError('Runway clip exceeds the 100MB download limit')
                        handle.write(chunk)
                if not size:
                    raise VideoProviderError('Runway returned an empty clip')
            partial.replace(path)
        except requests.RequestException:
            raise VideoProviderError('Runway media transfer failed; refresh the existing task without a new generation') from None
        finally:
            partial.unlink(missing_ok=True)

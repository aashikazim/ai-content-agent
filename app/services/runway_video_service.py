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
from pydantic import Field
from runwayml import APIConnectionError, APIError, APIStatusError, BaseModel, RunwayML

from app.services.gemini_video_service import VideoProviderError
from app.services.runway_models import EDIT_MODELS, capabilities, ratio, validate

TASK_ID = re.compile(r'^[A-Za-z0-9_-]{1,200}$')


class UploadTarget(BaseModel):
    upload_url: str = Field(alias='uploadUrl')
    runway_uri: str = Field(alias='runwayUri')
    fields: dict[str, str]


class RunwayVideoService:
    def __init__(self, config, *, client=None, session=None, model=None):
        self.config = config
        self.model = model or config.video_model
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
               operation='generate', duration=None, start_image=None, source_video=None):
        if operation == 'edit':
            if self.model not in EDIT_MODELS or not source_video:
                raise VideoProviderError('Choose an explicit supported Runway edit model and a verified source clip')
            if len(prompt) > (1000 if self.model == 'aleph2' else 26000 if self.model == 'kling_4' else 2500):
                raise VideoProviderError('Edit instructions exceed this model\'s prompt limit')
            uri = self.upload_media(source_video)
            payload = {'model':self.model, 'prompt_text':prompt}
            if self.model.startswith('klingO3'):
                payload.update(prompt_video=uri, provider_settings={'keep_original_sound':True})
                if references:
                    payload['references'] = [{'uri':self.image(data)} for data in references]
            else:
                payload['video_uri'] = uri
                if self.model == 'kling_4':
                    payload['ratio'] = 'auto_' + self.config.resolution
                    if references:
                        payload['references'] = [{'uri':self.image(data)} for data in references]
            created = self.call(lambda:self.sdk().video_to_video.create(**payload), paid=True)
            return self.created_snapshot(created)
        if operation == 'extend':
            continuation = RunwayVideoService(self.config.model_copy(update={'video_input_mode':'image'}), client=self.sdk(), session=self.session)
            return continuation.submit(prompt,aspect_ratio=aspect_ratio,references=references,duration=duration,start_image=start_image)
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
        return self.created_snapshot(created)

    @staticmethod
    def created_snapshot(created):
        identifier = getattr(created, 'id', None)
        if not isinstance(identifier, str) or not TASK_ID.fullmatch(identifier):
            raise VideoProviderError('Runway submission returned no usable task ID. Inspect billing and task history; do not resubmit.', uncertain=True)
        return {'interaction_id': identifier, 'status': 'in_progress', 'usage': {}}

    def upload_media(self, path):
        path = Path(path)
        if not path.is_file() or not 0 < path.stat().st_size <= 100 * 1024 * 1024:
            raise VideoProviderError('The edit source must be a nonempty local video below 100MB')
        if path.stat().st_size <= 3 * 1024 * 1024:
            return 'data:video/mp4;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
        # Use the official SDK for the upload reservation. Its convenience
        # multipart uploader creates a separate proxy-inheriting client; send
        # the signed multipart data with our credential-free secure session.
        target = self.call(lambda:self.sdk().post('/v1/uploads', cast_to=UploadTarget,
            body={'filename':'source.mp4','type':'ephemeral'}))
        upload_url = getattr(target,'upload_url',None)
        uri = getattr(target,'runway_uri',None)
        fields = getattr(target,'fields',None)
        self.validate_url(upload_url, upload=True)
        if not isinstance(uri,str) or not uri.startswith('runway://') or not isinstance(fields,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in fields.items()):
            raise VideoProviderError('Runway returned an invalid temporary upload identifier')
        try:
            with path.open('rb') as handle:
                response = self.session.post(upload_url,headers={},data=fields,
                    files={'file':('source.mp4',handle,'video/mp4')},timeout=(5,120),allow_redirects=False)
            if response.status_code not in {200,201,204}:
                raise VideoProviderError('Runway source-media transfer failed before generation; inspect the attempt')
        except requests.RequestException:
            raise VideoProviderError('Runway source-media transfer was interrupted before generation; no generation retry was sent') from None
        return uri

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
    def validate_url(url, *, upload=False):
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower()
            # Fail closed for a new CDN rather than fetching arbitrary URLs.
            suffixes = ('cloudfront.net', 'runwayml.com', 'runwaycdn.com') + (('amazonaws.com',) if upload else ())
            trusted = any(host.endswith('.' + suffix) for suffix in suffixes)
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

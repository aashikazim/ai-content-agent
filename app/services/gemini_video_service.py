"""Google Interactions REST adapter. One paid POST, then retrieval only.

Official contract: https://aistudio.google.com/docs/omni and /docs/background-execution.
No SDK, credential-bearing URLs, retries, automatic redirects or model substitution.
"""
import base64
import binascii
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

MODEL = 'gemini-omni-1.1-flash'
BASE = 'https://generativelanguage.googleapis.com/v1beta'
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 140 * 1024 * 1024


class VideoProviderError(RuntimeError):
    def __init__(self, message, *, uncertain=False):
        self.uncertain = uncertain
        super().__init__(message)


def interaction_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
        raise VideoProviderError('Google returned an invalid interaction ID', uncertain=True)
    return value


class GeminiVideoService:
    def __init__(self, session=None):
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        if isinstance(self.session, requests.Session):
            self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))

    def headers(self):
        key = os.getenv('GEMINI_API_KEY')
        if not key:
            raise VideoProviderError('GEMINI_API_KEY is not configured')
        return {'x-goog-api-key': key, 'Content-Type': 'application/json', 'Api-Revision': '2026-05-20'}

    def call(self, method, path, payload=None):
        try:
            response = self.session.request(method, BASE + path, headers=self.headers(),
                json=payload, timeout=(5, 180 if method == 'POST' else 60),
                allow_redirects=False, stream=True)
            with response:
                if response.status_code not in ({200,201,202} if method == 'POST' else {200}):
                    raise VideoProviderError(
                        'Google rejected the request; check model access, quota and settings. No automatic retry was sent.',
                        uncertain=method == 'POST' and (response.status_code >= 500 or response.status_code in {408, 409}),
                    )
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > MAX_JSON_BYTES:
                        raise VideoProviderError('Google response exceeded the download limit', uncertain=method == 'POST')
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except requests.RequestException:
            raise VideoProviderError('Google request outcome is unknown. Retrieve an existing interaction; never repeat a paid submission automatically.', uncertain=method == 'POST') from None
        except (ValueError, TypeError):
            raise VideoProviderError('Google returned an invalid response; inspect generation history', uncertain=method == 'POST') from None

    def check_model(self):
        self.call('GET', '/models/' + MODEL)

    def submit(self, prompt, *, aspect_ratio, references=(), previous_id=None, operation='generate'):
        if operation not in {'generate', 'edit', 'extend'} or aspect_ratio not in {'9:16', '16:9'}:
            raise ValueError('Unsupported Gemini video request')
        if len(references) > 3 or len(prompt) > 20000:
            raise ValueError('Gemini input exceeds application limits')
        content = [{'type': 'image', 'mime_type': 'image/png', 'data': base64.b64encode(raw).decode()} for raw in references]
        content.append({'type': 'text', 'text': prompt})
        task = 'reference_to_video' if references else 'text_to_video'
        payload = {'model': MODEL, 'input': content, 'background': True, 'store': True, 'stream': False,
            'response_format': {'type': 'video', 'delivery': 'uri', 'resolution': '720p', 'aspect_ratio': aspect_ratio},
            'generation_config': {'video_config': {'task': task if operation == 'generate' else operation}}}
        if previous_id:
            payload['previous_interaction_id'] = interaction_id(previous_id)
        elif operation != 'generate':
            raise ValueError('Edits and extensions require an existing interaction ID')
        # Duration's string syntax is not documented. Request it in the prompt,
        # then enforce measured duration locally instead of inventing an API field.
        if len(json.dumps(payload).encode()) > 10 * 1024 * 1024:
            raise ValueError('Reference input exceeds the application byte limit')
        return self.snapshot(self.call('POST', '/interactions', payload))

    def retrieve(self, identifier):
        return self.snapshot(self.call('GET', '/interactions/' + interaction_id(identifier)))

    @staticmethod
    def snapshot(body):
        identifier = interaction_id(body.get('id'))
        status = body.get('status')
        if not isinstance(status, str):
            status = 'needs_reconciliation'
        if status not in {'in_progress', 'completed', 'failed', 'cancelled', 'incomplete', 'requires_action', 'requires_approval'}:
            status = 'needs_reconciliation'
        steps = body.get('steps') if isinstance(body.get('steps'), list) else []
        videos = [part for step in steps if isinstance(step, dict) and step.get('type') == 'model_output'
                  for part in (step.get('content') if isinstance(step.get('content'), list) else [])
                  if isinstance(part, dict) and part.get('type') == 'video']
        usage = body.get('usage') or {}
        return {'interaction_id': identifier, 'status': status,
                'video': videos[-1] if videos else None,
                'usage': usage if isinstance(usage, dict) else {}}

    def download(self, snapshot, destination):
        identifier = interaction_id(snapshot['interaction_id'])
        part = snapshot.get('video') or {}
        # Retrieval currently returns inline video even when creation used URI delivery.
        if not part.get('data'):
            part = self.retrieve(identifier).get('video') or {}
        raw = None
        if part.get('data'):
            try:
                encoded = part['data']
                if not isinstance(encoded, str) or len(encoded) > (MAX_VIDEO_BYTES * 4 // 3 + 8):
                    raise ValueError
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise VideoProviderError('Google returned invalid or oversized video data') from None
        elif part.get('uri'):
            # Never send credentials to arbitrary media hosts or follow redirects.
            url = urlsplit(part['uri'])
            if (url.scheme != 'https' or url.netloc != 'generativelanguage.googleapis.com'
                    or not re.fullmatch(r'/v1beta/files/[A-Za-z0-9_-]+:download', url.path)
                    or url.query not in {'', 'alt=media'} or url.fragment):
                raise VideoProviderError('Google media location is unsupported; retrieve the interaction for inline data')
            try:
                with self.session.get('https://' + url.netloc + url.path + '?alt=media',
                        headers=self.headers(), timeout=(5, 120), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        raise VideoProviderError('Video download failed; retrieve the existing interaction again')
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        if len(data) > MAX_VIDEO_BYTES:
                            raise VideoProviderError('Generated video exceeds the application download limit')
                    raw = bytes(data)
            except requests.RequestException:
                raise VideoProviderError('Video download was interrupted; retrieve the existing interaction again') from None
        if not raw or len(raw) > MAX_VIDEO_BYTES:
            raise VideoProviderError('Completed interaction has no usable video')
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        from uuid import uuid4
        temporary = path.with_name(path.name + '.' + str(uuid4()) + '.download')
        try:
            temporary.write_bytes(raw)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

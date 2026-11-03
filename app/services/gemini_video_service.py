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
LEGACY_FAILURE_MESSAGE = 'Provider generation did not complete; inspect history before a separately authorized change'

# Provider messages may echo submitted material or credentials. Expose known
# diagnostic codes with application-owned guidance instead of raw error bodies.
ERROR_GUIDANCE = {
    'RESOURCE_EXHAUSTED': 'Google quota or capacity is exhausted. Check the Gemini project billing, credits and model rate limits in Google AI Studio before authorizing another attempt.',
    'UNAUTHENTICATED': 'Google rejected the configured credentials. Check the server API key and its restrictions.',
    'PERMISSION_DENIED': 'Google denied model or project access. Check project permissions, API-key restrictions and model availability.',
    'FAILED_PRECONDITION': 'Google project prerequisites are not satisfied. Check model availability, billing and project settings.',
    'INVALID_ARGUMENT': 'Google rejected the video settings. Check the selected model and supported request parameters.',
    'NOT_FOUND': 'The Google model or stored interaction was not found. Check model access and interaction retention.',
    'UNAVAILABLE': 'Google video generation is temporarily unavailable. Inspect provider status before a separately authorized attempt.',
    'INTERNAL': 'Google could not process this video request. Inspect provider usage before a separately authorized attempt.',
    'DEADLINE_EXCEEDED': 'Google did not finish the request in time. Retrieve the existing interaction and inspect usage before another attempt.',
    'CANCELLED': 'Google reported that the request was cancelled. Inspect usage before authorizing a new attempt.',
    'UNKNOWN_PROVIDER_ERROR': 'Google reported a generation failure without a recognized diagnostic code. Inspect Google AI Studio usage and project settings.',
}
HTTP_ERROR_CODES = {400: 'INVALID_ARGUMENT', 401: 'UNAUTHENTICATED', 403: 'PERMISSION_DENIED',
                    404: 'NOT_FOUND', 408: 'DEADLINE_EXCEEDED', 429: 'RESOURCE_EXHAUSTED',
                    500: 'INTERNAL', 503: 'UNAVAILABLE', 504: 'DEADLINE_EXCEEDED'}


def safe_provider_errors(body, *, http_status=None):
    """Normalize documented errors[] and HTTP error{} without reflecting text."""
    entries = body.get('errors') if isinstance(body.get('errors'), list) else []
    entries = entries[:5]
    if isinstance(body.get('error'), dict):
        entries = [body['error'], *entries][:5]
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = entry.get('status') or entry.get('code')
        if type(code) is int:
            code = HTTP_ERROR_CODES.get(code)
        if not isinstance(code, str) or code not in ERROR_GUIDANCE:
            code = 'UNKNOWN_PROVIDER_ERROR'
        if not any(item['code'] == code for item in result):
            result.append({'code': code, 'message': ERROR_GUIDANCE[code]})
    if not result and http_status in HTTP_ERROR_CODES:
        code = HTTP_ERROR_CODES[http_status]
        result.append({'code': code, 'message': ERROR_GUIDANCE[code]})
    return result


def provider_failure_message(snapshot):
    errors = safe_provider_errors(snapshot)
    if errors:
        return ' '.join(f"Google reported {item['code']}: {item['message']}" for item in errors[:2]) + ' No automatic retry was sent.'
    return 'Google generation did not complete and supplied no diagnostic code. Inspect provider usage before a separately authorized attempt; no automatic retry was sent.'


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
                accepted = response.status_code in ({200,201,202} if method == 'POST' else {200})
                data = bytearray()
                limit = MAX_JSON_BYTES if accepted else 65536
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        if accepted:
                            raise VideoProviderError('Google response exceeded the download limit', uncertain=method == 'POST')
                        break
                if not accepted:
                    try:
                        body = json.loads(data)
                        if not isinstance(body, dict):
                            body = {}
                    except (ValueError, TypeError):
                        body = {}
                    errors = safe_provider_errors(body, http_status=response.status_code)
                    raise VideoProviderError(
                        provider_failure_message({'errors': errors}) if errors else
                        f'Google rejected the request (HTTP {response.status_code}); check model access, quota and settings. No automatic retry was sent.',
                        uncertain=method == 'POST' and (response.status_code >= 500 or response.status_code in {408, 409}),
                    )
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
                'usage': usage if isinstance(usage, dict) else {},
                'errors': safe_provider_errors(body)}

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

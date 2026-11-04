"""Private, permission-confirmed motion inputs for local character animation."""
import base64
import binascii
import json
import os
import time
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services.cloud_video_storage import CloudVideoStorage
from app.services.cloud_video_renderer import probe, validate_clip, media_sha256

MAX_BYTES = 20 * 1024 * 1024


class DrivingUpload(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=80)
    rights_note: str = Field(min_length=1, max_length=500)
    rights_confirmed: bool = Field(default=False, strict=True)
    video_base64: str = Field(min_length=1, max_length=28 * 1024 * 1024, repr=False)


class DrivingReferenceService:
    def __init__(self, db_path):
        self.storage = CloudVideoStorage(db_path)
        self.root = Path(os.getenv('DRIVING_REFERENCES_DIR', 'data/driving_references')).resolve()

    @staticmethod
    def public(value):
        return {key: value[key] for key in ('reference_id', 'name', 'rights_note', 'sha256', 'created_at')} | {
            'quality': json.loads(value['quality_json']),
            'video_url': f"/api/v1/generation/driving-references/{value['reference_id']}/video",
        }

    def list_references(self):
        with self.storage.connect() as conn:
            return [self.public(dict(row)) for row in conn.execute('SELECT * FROM driving_references ORDER BY created_at DESC')]

    def verified(self, identifier):
        with self.storage.connect() as conn:
            row = conn.execute('SELECT * FROM driving_references WHERE reference_id=?', (str(identifier),)).fetchone()
        if not row:
            raise ValueError('Driving motion reference not found')
        value = dict(row)
        path = Path(value['video_path']).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or media_sha256(path) != value['sha256']:
            raise ValueError('Driving motion reference is missing or changed; upload a new permitted clip')
        return value

    def create(self, upload):
        if upload.rights_confirmed is not True:
            raise ValueError('Confirm permission to use this driving video and any depicted person')
        try:
            raw = base64.b64decode(upload.video_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Driving video must be valid base64') from None
        if not raw or len(raw) > MAX_BYTES:
            raise ValueError('Driving video must be at most 20MB')
        if raw[4:8] != b'ftyp':
            raise ValueError('Upload an MP4 driving video, not a playlist or URL')
        identifier = str(uuid4())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / (identifier + '.mp4')
        try:
            with path.open('xb') as file:
                os.chmod(path, 0o600)
                file.write(raw)
            info = probe(path)
            video = next((stream for stream in info['streams'] if stream['codec_type'] == 'video'), {})
            duration = float(info.get('format', {}).get('duration', 0))
            try:
                rate = float(Fraction(video.get('avg_frame_rate', '0/1')))
            except (ValueError, ZeroDivisionError):
                raise ValueError('Driving video has an invalid frame rate') from None
            if not 1 <= rate <= 60:
                raise ValueError('Driving video must have a frame rate between 1 and 60 FPS')
            if not 3 <= duration <= 10.5 or max(video.get('width', 0), video.get('height', 0)) > 1920:
                raise ValueError('Use a 3–10-second driving MP4, at most 1920 pixels on either side')
            if not {'mp4', 'mov'}.intersection(info.get('format', {}).get('format_name', '').split(',')):
                raise ValueError('Upload an MP4 driving video')
            quality = validate_clip(path, duration, require_audio=False)
            with self.storage.connect() as conn:
                conn.execute('INSERT INTO driving_references VALUES (?,?,?,?,?,?,?)',
                    (identifier, upload.name, upload.rights_note, str(path), quality['sha256'], json.dumps(quality), int(time.time())))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.public(self.verified(identifier))

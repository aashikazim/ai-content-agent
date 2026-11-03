"""Local, immutable reference registry; images are uploaded only with paid confirmation."""
import base64
import binascii
import hashlib
import io
import os
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from app.services.cloud_video_storage import CloudVideoStorage


class ReferenceUpload(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    character_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,31}$')
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    wardrobe: str = Field(default='', max_length=500)
    voice_notes: str = Field(default='', max_length=500)
    rights_note: str = Field(min_length=1, max_length=500)
    rights_confirmed: bool = Field(default=False, strict=True)
    image_base64: str = Field(min_length=1, max_length=6_000_000, repr=False)


class CharacterReferenceService:
    def __init__(self, db_path):
        self.storage = CloudVideoStorage(db_path)
        self.root = Path(os.getenv('CHARACTER_REFERENCES_DIR', 'data/character_references')).resolve()

    def create(self, request):
        request = ReferenceUpload.model_validate(request)
        if request.rights_confirmed is not True:
            raise ValueError('Confirm permission to use this image, including any depicted person')
        try:
            raw = base64.b64decode(request.image_base64, validate=True)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.width * source.height > 16_000_000 or min(source.size) < 128:
                        raise ValueError
                    image = ImageOps.exif_transpose(source).convert('RGB')
                    image.thumbnail((1024, 1024))
                    buffer = io.BytesIO()
                    image.save(buffer, format='PNG')
                    cleaned = buffer.getvalue()
            if len(cleaned) > 2 * 1024 * 1024:
                raise ValueError
        except (OSError, ValueError, binascii.Error, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError('Use a valid image: at least 128px, at most 16 megapixels and 4MB; normalized PNG must fit 2MB') from None
        self.root.mkdir(parents=True, exist_ok=True)
        identifier = str(uuid4())
        path = self.root / (identifier + '.png')
        path.write_bytes(cleaned)
        path.chmod(0o600)
        import time
        try:
            with self.storage.connect() as conn:
                conn.execute('INSERT INTO character_references VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (identifier, request.character_id, request.name, request.description, request.wardrobe,
                     request.voice_notes, request.rights_note, str(path), hashlib.sha256(cleaned).hexdigest(), int(time.time())))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.get(identifier)

    def get(self, identifier):
        with self.storage.connect() as conn:
            row = conn.execute('SELECT * FROM character_references WHERE reference_id = ?', (str(identifier),)).fetchone()
        if not row:
            raise ValueError('Character reference does not exist')
        return dict(row)

    def list_references(self):
        with self.storage.connect() as conn:
            rows = conn.execute('SELECT * FROM character_references ORDER BY created_at DESC,rowid DESC LIMIT 100').fetchall()
        return [self.public(dict(row)) for row in rows]

    @staticmethod
    def public(value):
        value = dict(value)
        value.pop('image_path', None)
        value['image_url'] = '/api/v1/generation/references/' + value['reference_id'] + '/image'
        return value

    def verified(self, identifiers):
        refs = [self.get(identifier) for identifier in identifiers]
        for ref in refs:
            path = Path(ref['image_path']).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
                raise ValueError('A character reference is missing or changed; upload a new reference')
        if len({ref['character_id'] for ref in refs}) != len(refs):
            raise ValueError('Choose one reference per character')
        return refs

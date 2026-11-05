"""Immutable, hash-bound cast packages over the existing private image registry."""
import hashlib
import json
import time
from uuid import UUID, uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.production_plan import CharacterProfile
from app.services.character_reference_service import CharacterReferenceService
from app.services.cloud_video_storage import CloudVideoStorage


class CastMember(CharacterProfile):
    wardrobe: str = Field(min_length=1, max_length=500)
    reference_ids: list[UUID] = Field(min_length=1, max_length=6)
    primary_reference_id: UUID
    tts_voice: Literal['en-us', 'en-us+m1', 'en-us+m2', 'en-us+f1', 'en-us+f2'] = 'en-us'
    ltx_adapter_id: UUID | None = None

    @model_validator(mode='after')
    def primary_belongs_to_character(self):
        if self.primary_reference_id not in self.reference_ids or len(set(self.reference_ids)) != len(self.reference_ids):
            raise ValueError('Choose a primary image from the unique reference images for this character')
        return self


class CastPackageUpload(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=100)
    characters: list[CastMember] = Field(min_length=1, max_length=3)
    location_notes: str = Field(default='', max_length=2000)
    confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def unique_cast(self):
        if len({member.character_id for member in self.characters}) != len(self.characters):
            raise ValueError('Character IDs must be unique')
        return self


class CinematicCastService:
    def __init__(self, db_path):
        self.storage = CloudVideoStorage(db_path)
        self.references = CharacterReferenceService(db_path)

    def create(self, value):
        value = CastPackageUpload.model_validate(value)
        if not value.confirmed:
            raise ValueError('Inspect and confirm these character images, outfits and voice descriptions')
        data = value.model_dump(mode='json', exclude={'confirmed'})
        assets = []
        for member in value.characters:
            # Each image is verified individually; multiple views belong to one actor.
            for identifier in member.reference_ids:
                reference = self.references.verified([identifier])[0]
                if reference['character_id'] != member.character_id:
                    raise ValueError('Every identity image must match its character ID')
                assets.append({'reference_id': reference['reference_id'], 'sha256': reference['sha256']})
        data['assets'] = assets
        from app.services.cinematic_adapter_service import CinematicAdapterService
        data['adapters'] = []
        for member in value.characters:
            if member.ltx_adapter_id:
                adapter=CinematicAdapterService().verified(member.ltx_adapter_id)
                data['adapters'].append({'character_id':member.character_id,'adapter_id':str(member.ltx_adapter_id),
                    'sha256':adapter['sha256'],'settings_sha256':adapter['settings_sha256']})
        encoded = json.dumps(data, sort_keys=True)
        identifier = str(uuid4())
        with self.storage.connect() as conn:
            conn.execute('INSERT INTO cinematic_cast_packages VALUES (?,?,?,?,?)',
                (identifier, data['name'], encoded, hashlib.sha256(encoded.encode()).hexdigest(), int(time.time())))
        return self.get(identifier)

    def get(self, identifier):
        with self.storage.connect() as conn:
            row = conn.execute('SELECT * FROM cinematic_cast_packages WHERE package_id=?', (str(identifier),)).fetchone()
        if not row:
            raise ValueError('Cast package does not exist')
        if hashlib.sha256(row['package_json'].encode()).hexdigest() != row['sha256']:
            raise ValueError('The cast package changed; create and inspect a new version')
        data = json.loads(row['package_json'])
        for item in data['assets']:
            if self.references.verified([item['reference_id']])[0]['sha256'] != item['sha256']:
                raise ValueError('A cast asset changed; create and inspect a new package')
        from app.services.cinematic_adapter_service import CinematicAdapterService
        for item in data.get('adapters',[]):
            adapter=CinematicAdapterService().verified(item['adapter_id'])
            if adapter['sha256']!=item['sha256'] or adapter['settings_sha256']!=item['settings_sha256']:
                raise ValueError('A cast adapter changed; inspect a new package')
        return {**data, 'package_id': row['package_id'], 'sha256': row['sha256'], 'created_at': row['created_at']}

    def list_packages(self):
        with self.storage.connect() as conn:
            rows = conn.execute('SELECT package_id FROM cinematic_cast_packages ORDER BY created_at DESC,rowid DESC LIMIT 100').fetchall()
        result = []
        for row in rows:
            try:
                result.append(self.get(row['package_id']))
            except ValueError:
                result.append({'package_id':row['package_id'],'name':'Unavailable cast package',
                    'characters':[],'unavailable':True})
        return result

    @staticmethod
    def bible(package):
        return json.dumps({key: package[key] for key in ('characters', 'location_notes')}, ensure_ascii=False)

    @staticmethod
    def bind(plan, package):
        known = {member['character_id']: member for member in package['characters']}
        if set(known) != {actor.character_id for actor in plan.characters}:
            raise ValueError('The screenplay changed the approved cast IDs; request an explicit revision')
        for actor in plan.characters:
            member = known[actor.character_id]
            if actor.name != member['name']:
                raise ValueError('The screenplay renamed an approved character')
            clothing = '. Clothing: ' + member['wardrobe']
            actor.description = member['description'][:1000-len(clothing)] + clothing
            actor.voice_notes = member['voice_notes']
        return plan

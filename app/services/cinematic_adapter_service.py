"""Explicit, immutable LTX-2.5 character adapter registration. No downloads/training."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
from uuid import UUID, uuid4

from app.services import local_cinematic_models as models
from app.services.local_cinematic_service import private_json


class CinematicAdapterService:
    def __init__(self):
        self.root = models.runtime_root()/'character_adapters'

    def register(self, source, *, name, license_note, trigger, scale=0.8, confirmed=False):
        if confirmed is not True:
            raise ValueError('Explicitly confirm this adapter was tested with the pinned LTX-2.5 distilled checkpoint')
        source=Path(source)
        if source.suffix!='.safetensors' or not source.is_file() or not 8 < source.stat().st_size <= 2*1024**3:
            raise ValueError('Use a local safetensors adapter no larger than 2GiB')
        with source.open('rb') as file:
            length=struct.unpack('<Q',file.read(8))[0]
            if not 0 < length <= 1024**2:
                raise ValueError('Invalid safetensors header')
            try:
                header=json.loads(file.read(length))
            except (ValueError,UnicodeError):
                raise ValueError('Invalid safetensors header') from None
        if not isinstance(header, dict) or not any('lora' in key.lower() for key in header if key!='__metadata__'):
            raise ValueError('No LoRA tensors found')
        if not name.strip() or len(name)>100 or not license_note.strip() or len(license_note)>500 or not trigger.strip() or len(trigger)>200:
            raise ValueError('Supply a name, permission/license note and short trigger description')
        if type(scale) not in (int,float) or not math.isfinite(scale) or not 0 < scale <= 1.5:
            raise ValueError('Adapter strength must be between 0 and 1.5')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        identifier=str(uuid4());target=self.root/(identifier+'.safetensors')
        shutil.copyfile(source,target);target.chmod(0o600)
        with target.open('rb') as file:
            digest=hashlib.file_digest(file,'sha256').hexdigest()
        value={'adapter_id':identifier,'name':name,'license_note':license_note,'trigger':trigger,'scale':scale,
            'sha256':digest,'model_repo':models.LTX_REPO,'model_revision':models.LTX_REVISION,
            'variant':'distilled','owner_confirmed_compatible':True}
        private_json(self.root/(identifier+'.json'),value)
        return value

    def verified(self, identifier):
        identifier=str(UUID(str(identifier)))
        try:
            value=json.loads((self.root/(identifier+'.json')).read_text())
            value={key:value[key] for key in ('adapter_id','name','license_note','trigger','scale','sha256','model_repo','model_revision','variant','owner_confirmed_compatible')}
            path=self.root/(identifier+'.safetensors')
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise ValueError
            with path.open('rb') as file:
                digest=hashlib.file_digest(file,'sha256').hexdigest()
            if (value['adapter_id']!=identifier or value['sha256']!=digest or value['model_repo']!=models.LTX_REPO or value['model_revision']!=models.LTX_REVISION
                    or value['variant']!='distilled' or value['owner_confirmed_compatible'] is not True):
                raise ValueError
            if type(value['scale']) not in (int,float) or not math.isfinite(value['scale']) or not 0 < value['scale'] <= 1.5:
                raise ValueError
        except (OSError,KeyError,ValueError,TypeError):
            raise ValueError('The character adapter is missing, changed or incompatible with the pinned local checkpoint') from None
        return {**value,'settings_sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest(),'path':str(path)}

    def list_adapters(self):
        result=[]
        for path in sorted(self.root.glob('*.json')):
            try:
                value=self.verified(path.stem);value.pop('path');result.append(value)
            except ValueError:
                continue
        return result

"""Preserve approved identity framing when native LTX center-crops its input.

No identity/wardrobe synthesis: contain the complete approved image on a blurred
copy of its own background. Register the derived scene frame separately; never
mutate the approved cast image or package. All geometry/provenance is hash-bound.
"""
import base64
import fcntl
import io
import json
from pathlib import Path
from typing import Any
from app.services.character_reference_service import CharacterReferenceService
from PIL import Image,ImageFilter,ImageOps
from app.automation.idempotency import fingerprint


def fit_reference(image:Image.Image,size:tuple[int,int])->Image.Image:
    source=image.convert('RGB')
    background=ImageOps.fit(source,size,method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(max(size)/18))
    foreground=ImageOps.contain(source,size,method=Image.Resampling.LANCZOS)
    background.paste(foreground,((size[0]-foreground.width)//2,(size[1]-foreground.height)//2))
    return background


def scene_frames(package:dict[str,Any],references:CharacterReferenceService,folder:Path,size:tuple[int,int])->dict[str,str]:
    result={}
    for actor in package['characters']:
        original=references.verified([actor['primary_reference_id']])[0]
        key=fingerprint({'package_sha256':package['sha256'],'reference_sha256':original['sha256'],'actor_id':actor['character_id'],'source_reference_id':original['reference_id'],'size':list(size),'fit':'contain_on_blur_v1'})
        directory=Path(folder)/key;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (directory/'prepare.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);metadata=directory/'provenance.json'
            if metadata.exists():
                value=json.loads(metadata.read_text())
                ref=references.verified([value['reference_id']])[0]
                if value['source_sha256']!=original['sha256'] or ref['sha256']!=value['derived_sha256'] or ref['character_id']!=actor['character_id']:raise ValueError('A prepared scene frame changed')
                result[actor['character_id']]=ref['reference_id'];continue
            with Image.open(original['image_path']) as image:prepared=fit_reference(image,size)
            output=io.BytesIO();prepared.save(output,format='PNG')
            value=references.create({**{k:actor[k] for k in ('character_id','name','description','wardrobe','voice_notes')},
                'rights_note':'Geometry-only preparation of owner-approved identity '+original['reference_id']+'; original face/clothing preserved',
                'rights_confirmed':True,'image_base64':base64.b64encode(output.getvalue()).decode()})
            proof={'source_reference_id':original['reference_id'],'source_sha256':original['sha256'],'reference_id':value['reference_id'],
                'derived_sha256':value['sha256'],'size':list(size),'method':'contain_on_blur_v1','identity_synthesis':False}
            temporary=metadata.with_suffix('.tmp');temporary.write_text(json.dumps(proof,indent=2));temporary.chmod(0o600);temporary.replace(metadata)
            result[actor['character_id']]=value['reference_id']
    return result

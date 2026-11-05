"""Reuse the verified cinematic runtime, cast packages and carry-frame protocol."""
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace

from app.services import local_cinematic_models as models
from app.services.cinematic_continuity import prepare_tail
from app.services.cloud_video_renderer import media_sha256, validate_clip
from app.services.local_cinematic_service import LocalCinematicService
from .generators import VideoGenerator
from .media import StudioError, assemble, extract_reference
from .storage import encoded

MODEL_IDS={'ltx':'ltx-2.5','wan':'wan2.2-animate-2','hybrid':'ltx-wan-hybrid'}


def native_config(settings):
    return SimpleNamespace(video_model=MODEL_IDS[settings.video_model],local_seed=settings.seed or 0,
        continuity_mode=settings.continuity if settings.cast_package_id else 'independent',
        cast_package_id=settings.cast_package_id,video_input_mode='image',
        narration='local_tts' if settings.video_model=='wan' else 'model_audio',
        start_image_reference_id=None,shot_image_reference_ids={},scene_image_reference_ids={})


def chunk_durations(seconds, maximum=10):
    if not 3 <= maximum <= 10:
        raise StudioError('Workflow max_duration_seconds must be between 3 and 10')
    count=math.ceil(seconds/maximum)
    # Balanced integer chunks avoid unsupported 1–2s final neural shots.
    base,extra=divmod(seconds,count)
    return [max(3,base+(i<extra)) for i in range(count)]


class NativeGenerator(VideoGenerator):
    def __init__(self, storage, attempt_id, stage=None):
        self.storage,self.attempt_id,self.stage=storage,attempt_id,stage

    def cached(self,index,request):
        with self.storage.connect() as conn:
            row=conn.execute('SELECT * FROM clip_studio_chunks WHERE attempt_id=? AND position=?',(self.attempt_id,index)).fetchone()
            if row:
                if json.loads(row['request_json'])!=request:raise StudioError('A cached chunk no longer matches its immutable dependencies')
                if row['status']=='submitting' and not row['external_id']:
                    raise StudioError('A generation acknowledgment is missing. Reconcile this attempt before another request',uncertain=True)
                return dict(row)
            conn.execute("INSERT INTO clip_studio_chunks VALUES (?,?,?,'queued',NULL,NULL,NULL)",(self.attempt_id,index,encoded(request)))
        return {'status':'queued','external_id':None}

    def update(self,index,**fields):
        if not set(fields)<={'status','external_id','output_path','quality_json'}:raise ValueError('Invalid chunk update')
        with self.storage.connect() as conn:
            conn.execute('UPDATE clip_studio_chunks SET '+','.join(k+'=?' for k in fields)+' WHERE attempt_id=? AND position=?',
                         (*fields.values(),self.attempt_id,index))

    def generate(self, clip, settings, seed, directory, *, references=None):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        config=native_config(settings);provider=LocalCinematicService(config);provider.check_model()
        cast=references.get('cast');actors={c['character_id']:c for c in (cast or {}).get('characters',[])}
        if settings.video_model=='wan' and (len(clip.character_ids)!=1 or references['source']['kind']=='images'):
            raise StudioError('Wan requires exactly one approved actor and a video driving source; use LTX or hybrid for images')
        reference,driver=extract_reference(references['source'],clip,directory/'source')
        files=[{'path':str(reference),'sha256':media_sha256(reference)}]
        identity_refs=references.get('identity_refs',[])
        files += [{'path':r['image_path'],'sha256':r['sha256']} for r in identity_refs]
        files += [{'path':a['path'],'sha256':a['sha256']} for a in references.get('adapters',[])]
        member=actors.get(clip.character_ids[0]) if len(clip.character_ids)==1 else None
        character=next((r for r in identity_refs if member and r['reference_id']==str(member['primary_reference_id'])),None)
        bible=' '.join(c['name']+': '+c['description']+'; clothing: '+c['wardrobe'] for c in actors.values() if c['character_id'] in clip.character_ids)
        dialogue=' '.join(actors[d.character_id]['name']+' says '+json.dumps(d.text)+' in '+actors[d.character_id]['voice_notes'] for d in clip.dialogue)
        prompt=clip.generation_prompt+'\nApproved identity and wardrobe: '+bible+'\nStyle: '+clip.style_notes+'\nLocation continuity: '+(cast or {}).get('location_notes','')+'\n'+dialogue
        previous=references.get('previous') if settings.continuity=='connected' and clip.transition=='continue' else None
        paths=[];provenance=[]
        for index,duration in enumerate(chunk_durations(clip.duration_seconds)):
            transition='continue' if index or previous else 'cut'
            route=models.route(config.video_model,{'character_ids':clip.character_ids,'transition':transition},
                               'connected' if transition=='continue' and settings.video_model!='wan' else 'anchored')
            frames=[]
            if previous and route in ('ltx','hybrid'):
                if media_sha256(previous['clip_path'])!=previous['source_sha256']:raise StudioError('The selected continuation source changed')
                frames=prepare_tail(previous,directory/f'carry-{index}')
            width,height=models.dimensions(settings.aspect_ratio,config.video_model)
            inputs={'route':route,'duration':duration,'shot_number':index+1,'seed':(seed+index)%2147483648,
                'width':width,'height':height,'reference_files':files+[{'path':f['path'],'sha256':f['sha256']} for f in frames],
                'start_image':character['image_path'] if character and transition=='cut' else str(reference),'character_image':character['image_path'] if character else None,
                'character_prompt':bible,'driving_video':str(driver) if driver else None,'continuation_frames':frames,
                'character_adapters':references.get('adapters',[]),
                'ltx_files':{k:str(models.runtime_root()/'models/ltx'/name) for k,name in models.LTX_FILES.items()}}
            if route in ('wan','hybrid') and not character:raise StudioError('Select a cast package with a primary image for the planned actor')
            saved_request={'inputs':inputs,'prompt':prompt,'negative_prompt':clip.negative_prompt,'previous':previous,
                           'aspect_ratio':settings.aspect_ratio}
            cached=self.cached(index,saved_request)
            path=directory/f'chunk-{index}.mp4'
            if cached['status']=='completed':
                quality=json.loads(cached['quality_json'])
                if not path.is_file() or media_sha256(path)!=quality['sha256']:raise StudioError('A cached chunk changed')
            else:
                if not cached['external_id']:
                    self.update(index,status='submitting')
                    snapshot=provider.submit(prompt+'\nAvoid: '+clip.negative_prompt,aspect_ratio=settings.aspect_ratio,local_inputs=inputs)
                    identifier=snapshot['interaction_id'];self.update(index,status='processing',external_id=identifier)
                else:identifier=cached['external_id']
                deadline=time.monotonic()+18000
                while True:
                    snapshot=provider.retrieve(identifier)
                    if snapshot['status']=='completed':break
                    if snapshot['status'] not in ('in_progress','processing','pending'):
                        self.update(index,status='failed');raise StudioError('Local neural generation failed; earlier completed chunks are preserved')
                    if self.stage:self.stage(f"generate:{references['position']+1}",.3,f"Clip {references['position']+1}, chunk {index+1}: {snapshot.get('usage',{}).get('stage','queued')}")
                    if time.monotonic()>deadline:raise StudioError('Local generation is still pending; reconcile by its saved ID',uncertain=True)
                    time.sleep(3)
                provider.download(snapshot,path)
                quality=validate_clip(path,duration,require_audio=False,expected_fps=24)
                quality['runtime_usage']=snapshot.get('usage',{})
                self.update(index,status='completed',output_path=str(path),quality_json=encoded(quality))
            paths.append(path)
            provenance.append({'index':index,'route':route,'seed':inputs['seed'],'sha256':quality['sha256'],
                               'source_dependency':{k:v for k,v in previous.items() if k!='clip_path'} if previous else None,'usage':quality.get('runtime_usage',{})})
            previous={'clip_path':str(path),'source_sha256':quality['sha256'],'source_trim':0,'source_duration':duration}
        output,thumbnail=assemble(paths,directory/'clip.mp4',clip.duration_seconds,settings.aspect_ratio,audio=references.get('audio'))
        quality=validate_clip(output,clip.duration_seconds,require_audio=False,expected_fps=24)
        quality.update(generator='native',chunks=provenance,cast_sha256=(cast or {}).get('sha256'),
                       actor_ids=clip.character_ids,continuity=settings.continuity,
                       previous_attempt_id=references.get('previous_attempt_id'))
        return output,thumbnail,quality

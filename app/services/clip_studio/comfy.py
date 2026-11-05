"""Explicit user-supplied ComfyUI API workflow bindings; no model substitution."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import requests

from app.services import local_cinematic_models as models
from app.services.cloud_video_renderer import media_sha256, validate_clip
from .media import StudioError, assemble, extract_reference
from .native import NativeGenerator, chunk_durations
from .storage import encoded


def inject_workflow(template, values):
    """Each binding names an exported API node ID and its exact input key."""
    if not isinstance(template,dict) or template.get('configured') is not True or not isinstance(template.get('workflow'),dict):
        raise StudioError('ComfyUI workflow is a placeholder. Export an API workflow and configure its bindings first')
    workflow=copy.deepcopy(template['workflow'])
    bindings=template.get('bindings',{})
    if not isinstance(bindings,dict):raise StudioError('ComfyUI bindings must be a dictionary')
    maximum=template.get('max_duration_seconds',10)
    if type(maximum) is not int or not 3<=maximum<=10:raise StudioError('Workflow max_duration_seconds must be an integer between 3 and 10')
    if str(template.get('output_node_id')) not in workflow:raise StudioError('Map output_node_id to the actual video save node')
    required={'prompt','negative_prompt','reference','duration','width','height','seed'}
    if not required<=set(bindings):raise StudioError('ComfyUI workflow is missing required bindings')
    used=set()
    for name,binding in bindings.items():
        if not isinstance(binding,list) or not binding:raise StudioError('A workflow binding must be [node_id,input_key]')
        targets=binding if binding and isinstance(binding[0],list) else [binding]
        for target in targets:
            if not isinstance(target,list) or len(target)!=2:raise StudioError('A workflow binding must be [node_id,input_key]')
            pair=tuple(map(str,target))
            if pair[0] not in workflow or not isinstance(workflow[pair[0]],dict) or not isinstance(workflow[pair[0]].get('inputs'),dict) or pair[1] not in workflow[pair[0]]['inputs']:
                raise StudioError('Workflow binding does not match its exported node inputs')
            if pair in used:raise StudioError('Each workflow value needs a distinct node input; identity and scene conditioning cannot overwrite each other')
            used.add(pair)
    for name,value in values.items():
        if name not in bindings:
            if name in ('character_reference','continuation_reference','source_video') and value:
                raise StudioError('The workflow does not declare the requested identity/motion/continuation conditioning binding')
            continue
        binding=bindings[name]
        targets=binding if binding and isinstance(binding[0],list) else [binding]
        for target in targets:
            if not isinstance(target,list) or len(target)!=2:raise StudioError('A workflow binding must be [node_id,input_key]')
            node,key=map(str,target)
            if node not in workflow or key not in workflow[node].get('inputs',{}):raise StudioError('Workflow binding does not match its exported node inputs')
            workflow[node]['inputs'][key]=value
    return workflow


class ComfyAdapter(NativeGenerator):
    model='ltx'
    def __init__(self,*args,session=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.session=session or requests.Session();self.session.trust_env=False
        self.session.mount('http://',requests.adapters.HTTPAdapter(max_retries=0))
        self.session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))
        self.base=os.getenv('COMFYUI_BASE_URL','http://127.0.0.1:8188').rstrip('/')
        url=urlsplit(self.base)
        if url.scheme not in ('http','https') or url.username or url.password or url.query or url.fragment:
            raise StudioError('Configure a plain trusted COMFYUI_BASE_URL on the server')

    @staticmethod
    def template(model):
        folder=Path(os.getenv('CLIP_STUDIO_WORKFLOWS_DIR',str(Path(__file__).absolute().parents[3]/'workflows/clip_studio')))
        try:return json.loads((folder/(model+'.json')).read_text())
        except (OSError,ValueError):raise StudioError('ComfyUI workflow template could not be read') from None

    def upload(self,path):
        path=Path(path)
        # ComfyUI's upload/image endpoint also accepts input assets used by video loaders.
        with path.open('rb') as data:
            response=self.session.post(self.base+'/upload/image',files={'image':(str(uuid4())+path.suffix,data,'application/octet-stream')},
                data={'type':'input','overwrite':'false'},timeout=(10,120),allow_redirects=False)
        if response.status_code!=200:raise StudioError('ComfyUI input upload failed before generation')
        value=response.json();name=value.get('name','');folder=value.get('subfolder','')
        if Path(name).name!=name or '..' in Path(folder).parts or Path(folder).is_absolute():raise StudioError('ComfyUI returned an unsafe asset name')
        return '/'.join(filter(None,(folder,name)))

    def generate(self,clip,settings,seed,directory,*,references=None):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if settings.video_model=='hybrid':raise StudioError('ComfyUI uses explicit LTX or Wan workflows; select native for the existing connected hybrid pipeline')
        template=self.template(self.model)
        with self.storage.connect() as conn:
            conn.execute('UPDATE clip_studio_render_attempts SET workflow_sha256=? WHERE attempt_id=?',(hashlib.sha256(encoded(template).encode()).hexdigest(),self.attempt_id))
        # Validate before uploads or GPU requests.
        inject_workflow(template,{'prompt':'','negative_prompt':'','reference':'','duration':3,'width':896,'height':512,'seed':0})
        ref,driver=extract_reference(references['source'],clip,directory/'source')
        identity_refs=references.get('identity_refs',[]);cast=references.get('cast') or {}
        actors={a['character_id']:a for a in cast.get('characters',[])}
        member=actors.get(clip.character_ids[0]) if len(clip.character_ids)==1 else None
        identity=next((r for r in identity_refs if member and str(member['primary_reference_id'])==r['reference_id']),None)
        if self.model=='wan' and (not identity or not driver):raise StudioError('Wan ComfyUI requires one approved actor and a source motion video')
        if len(clip.character_ids)>1 and not template.get('supports_multiple_characters'):
            raise StudioError('This ComfyUI workflow does not declare multi-character conditioning; use native LTX')
        previous=references.get('previous') if clip.transition=='continue' and settings.continuity=='connected' else None
        paths=[];records=[]
        lockroot=models.runtime_root();lockroot.mkdir(parents=True,exist_ok=True)
        with (lockroot/'gpu.lock').open('a') as lock:
            # Shared with existing local generation; the separate analysis worker is CPU only.
            fcntl.flock(lock,fcntl.LOCK_EX)
            for index,duration in enumerate(chunk_durations(clip.duration_seconds,int(template.get('max_duration_seconds',10)))):
                carry=None
                if previous and self.model=='ltx':
                    from app.services.cinematic_continuity import prepare_tail
                    carry=Path(prepare_tail(previous,directory/f'carry-{index}')[-1]['path'])
                prompt=clip.generation_prompt+'\n'+clip.style_notes+'\n'+' '.join(a['name']+': '+a['description']+'; clothing: '+a['wardrobe'] for a in actors.values() if a['character_id'] in clip.character_ids)+'\n'+' '.join(actors[d.character_id]['name']+' says '+json.dumps(d.text) for d in clip.dialogue)
                # File digests and bindings are pinned independently of temporary upload filenames.
                state={'template_sha256':hashlib.sha256(encoded(template).encode()).hexdigest(),'prompt':prompt,
                    'negative_prompt':clip.negative_prompt,'seed':(seed+index)%2147483648,'duration':duration,
                    'reference_sha256':media_sha256(ref),'identity_sha256':identity['sha256'] if identity else None,
                    'continuation':previous,'source_sha256':media_sha256(driver) if driver else None,'aspect':settings.aspect_ratio}
                cached=self.cached(index,state);path=directory/f'chunk-{index}.mp4'
                if cached['status']=='completed':
                    quality=json.loads(cached['quality_json'])
                    if not path.is_file() or media_sha256(path)!=quality['sha256']:raise StudioError('A ComfyUI chunk changed after validation')
                else:
                    if not cached['external_id']:
                        width,height=models.dimensions(settings.aspect_ratio,'wan2.2-animate-2' if self.model=='wan' else 'ltx-2.5')
                        values={'prompt':prompt,'negative_prompt':clip.negative_prompt,'reference':self.upload(ref),
                            'duration':duration,'width':width,'height':height,'seed':state['seed']}
                        if identity:values['character_reference']=self.upload(identity['image_path'])
                        if carry:values['continuation_reference']=self.upload(carry)
                        if driver and clip.reference_mode=='video-to-video':values['source_video']=self.upload(driver)
                        workflow=inject_workflow(template,values)
                        self.update(index,status='submitting')
                        try:response=self.session.post(self.base+'/prompt',json={'prompt':workflow,'client_id':self.attempt_id},timeout=(10,60),allow_redirects=False)
                        except requests.RequestException:raise StudioError('ComfyUI acknowledgment is uncertain; inspect its queue before reconciliation',uncertain=True) from None
                        if response.status_code!=200:
                            uncertain=response.status_code>=500
                            self.update(index,status='submitting' if uncertain else 'failed')
                            raise StudioError('ComfyUI rejected the workflow or returned an uncertain result',uncertain=uncertain)
                        try:identifier=str(UUID(response.json()['prompt_id']))
                        except (KeyError,ValueError,TypeError):raise StudioError('ComfyUI did not return a valid prompt ID; inspect its queue',uncertain=True) from None
                        self.update(index,status='processing',external_id=identifier)
                    else:identifier=cached['external_id']
                    deadline=time.monotonic()+18000
                    while True:
                        response=self.session.get(self.base+'/history/'+identifier,timeout=(10,60),allow_redirects=False)
                        if response.status_code!=200:raise StudioError('ComfyUI history is unavailable; poll the saved ID before regeneration',uncertain=True)
                        history=response.json().get(identifier)
                        if history:
                            status=history.get('status',{})
                            if status.get('status_str')=='error':self.update(index,status='failed');raise StudioError('ComfyUI execution failed; inspect its workflow nodes')
                            if status.get('completed'):break
                        if time.monotonic()>deadline:raise StudioError('ComfyUI task is still pending; inspect its saved prompt ID',uncertain=True)
                        time.sleep(3)
                    output=history.get('outputs',{}).get(str(template.get('output_node_id')), {})
                    videos=[v for key in ('gifs','videos','images') for v in output.get(key,[]) if Path(v.get('filename','')).suffix.lower() in ('.mp4','.webm','.mov')]
                    if len(videos)!=1:raise StudioError('Map output_node_id to a node producing exactly one video')
                    asset=videos[0]
                    if Path(asset['filename']).name!=asset['filename'] or '..' in Path(asset.get('subfolder','')).parts or Path(asset.get('subfolder','')).is_absolute() or asset.get('type','output')!='output':raise StudioError('Unsafe ComfyUI output path')
                    with self.session.get(self.base+'/view',params={k:asset[k] for k in ('filename','subfolder','type') if k in asset},stream=True,timeout=(10,120),allow_redirects=False) as response:
                        if response.status_code!=200:raise StudioError('ComfyUI output download failed; reuse its saved prompt ID')
                        size=0
                        with path.open('wb') as file:
                            for block in response.iter_content(1024**2):
                                size+=len(block)
                                if size>256*1024**2:raise StudioError('ComfyUI chunk exceeds 256MB')
                                file.write(block)
                    quality=validate_clip(path,duration,require_audio=False,expected_fps=24)
                    self.update(index,status='completed',output_path=str(path),quality_json=encoded(quality))
                paths.append(path);records.append({**state,'continuation':{k:v for k,v in previous.items() if k!='clip_path'} if previous else None})
                previous={'clip_path':str(path),'source_sha256':quality['sha256'],'source_trim':0,'source_duration':duration}
            # Release model memory before native jobs acquire the same DGX lock.
            try:self.session.post(self.base+'/free',json={'unload_models':True,'free_memory':True},timeout=(5,30),allow_redirects=False)
            except requests.RequestException:pass
        output,thumbnail=assemble(paths,directory/'clip.mp4',clip.duration_seconds,settings.aspect_ratio,audio=references.get('audio'))
        quality=validate_clip(output,clip.duration_seconds,require_audio=False,expected_fps=24)
        quality.update(generator='comfyui',chunks=records,cast_sha256=cast.get('sha256'),previous_attempt_id=references.get('previous_attempt_id'))
        return output,thumbnail,quality


class LtxAdapter(ComfyAdapter):model='ltx'
class WanAdapter(ComfyAdapter):model='wan'

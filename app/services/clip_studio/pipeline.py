"""Resumable orchestration for real Clip Studio jobs, isolated from the mock path."""
import json
from pathlib import Path
import time
from uuid import uuid4

from app.services.cinematic_cast_service import CinematicCastService
from app.services.cloud_video_renderer import media_sha256, run
from app.services.tts_service import TTSService
from .comfy import LtxAdapter, WanAdapter
from .media import StudioError, analyze, probe_source
from .native import NativeGenerator
from .planner import HostedPlanner
from .schemas import PlannedClip
from .storage import encoded


def audio_track(clip, settings, source, cast, directory):
    directory.mkdir(parents=True,exist_ok=True)
    if settings.narration=='source' or settings.reattach_audio:
        if source['kind']=='images' or not probe_source(source['path'])['audio']:raise StudioError('The selected source has no audio to reattach')
        output=directory/'source.wav'
        run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(clip.source_start),'-i',source['path'],
             '-t',str(min(clip.duration_seconds,clip.source_end-clip.source_start)),'-vn','-ar','48000','-ac','2',str(output)])
        return output
    if settings.narration=='character_tts':
        if not clip.dialogue:raise StudioError('Character narration needs dialogue in the approved plan')
        voices={a['character_id']:a['tts_voice'] for a in (cast or {}).get('characters',[])}
        pieces=[]
        for index,line in enumerate(clip.dialogue):
            if line.character_id not in voices:raise StudioError('Dialogue must use an approved cast voice')
            path=directory/f'voice-{index}.wav';TTSService(voice=voices[line.character_id]).synthesize(line.text,path);pieces.append(path)
        manifest=directory/'voices.txt';manifest.write_text(''.join(f"file '{p.name}'\n" for p in pieces))
        output=directory/'dialogue.wav'
        run(['ffmpeg','-v','error','-nostdin','-y','-f','concat','-safe','1','-i',str(manifest),'-ar','48000','-ac','2',str(output)])
        from app.services.cloud_video_renderer import probe
        if float(probe(output)['format']['duration'])>clip.duration_seconds+.25:raise StudioError('Dialogue exceeds the clip duration. Shorten dialogue or request a longer clip; speech was not silently truncated')
        return output
    return None


class RealPipeline:
    def __init__(self,service):self.service=service;self.storage=service.storage

    def run(self,row,request,payload,stage,generator=None):
        service=self.service;project_id=row['project_id']
        cast=CinematicCastService(self.storage.db_path).get(request.settings.cast_package_id) if request.settings.cast_package_id else None
        # A cast package is immutable and reverified every run.
        refs=CinematicCastService(self.storage.db_path).references.verified([a['reference_id'] for a in cast['assets']]) if cast else []
        adapters=[]
        if cast and cast.get('adapters'):
            from app.services.cinematic_adapter_service import CinematicAdapterService
            adapters=[(a['character_id'],CinematicAdapterService().verified(a['adapter_id'])) for a in cast['adapters']]
        stage('ingest',.03,'Validating source integrity; real generation stays in the existing isolated runtime')
        with self.storage.connect() as conn:
            sources=[dict(s) for s in conn.execute('SELECT * FROM clip_studio_sources WHERE project_id=? ORDER BY position',(project_id,))]
            saved=conn.execute('SELECT result_json FROM clip_studio_analyses WHERE project_id=?',(project_id,)).fetchone()
        for source in sources:
            if source['path'] and media_sha256(source['path'])!=source['sha256']:raise StudioError('Source integrity changed; create a new project')
        if saved:analysis=json.loads(saved[0])
        elif request.source_kind=='images':analysis={'scenes':[],'transcript':[],'images':len(sources)}
        else:
            stage('analyze',.1,'Downloading if needed, detecting scenes and transcribing speech on CPU')
            result=analyze(sources[0],service.root/project_id/'analysis',request.settings.whisper_model,service.upload_limit())
            metadata=probe_source(result['path']);source=sources[0]
            source.update(path=result.pop('path'),sha256=None,metadata_json=encoded(metadata))
            source['sha256']=media_sha256(source['path']);analysis=result
            with self.storage.connect() as conn:
                conn.execute('UPDATE clip_studio_sources SET path=?,sha256=?,metadata_json=? WHERE source_id=?',
                             (source['path'],source['sha256'],source['metadata_json'],source['source_id']))
        with self.storage.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO clip_studio_analyses VALUES (?,?,?)',(project_id,encoded(analysis),int(time.time())))
            clips_exist=conn.execute('SELECT 1 FROM clip_studio_clips WHERE project_id=?',(project_id,)).fetchone()
        if not clips_exist:
            with self.storage.connect() as conn:
                uncertain=conn.execute("SELECT 1 FROM clip_studio_calls WHERE project_id=? AND status IN ('submitting','uncertain')",(project_id,)).fetchone()
            if uncertain:raise StudioError('An earlier paid planning request is unresolved. Check provider usage and reconcile it before another request',uncertain=True)
            if not payload.get('planning_authorized',False):raise StudioError('Explicit paid planning confirmation is required')
            stage('plan',.2,'Requesting the selected planner; at most one known-invalid JSON repair is authorized')
            plan,usage=HostedPlanner(self.storage,project_id).plan(request,sources,analysis,cast)
            plan_id=str(uuid4())
            with self.storage.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('INSERT INTO clip_studio_plans VALUES (?,?,?,?,?,?,?,?,?)',
                    (plan_id,project_id,1,request.settings.planner_provider,request.settings.planner_model,plan.model_dump_json(),usage[-1]['id'],encoded(usage),int(time.time())))
                for index,clip in enumerate(plan.clips):
                    identifier=str(uuid4())
                    conn.execute('INSERT INTO clip_studio_clips VALUES (?,?,?,?,?,?,?,?,NULL)',
                        (identifier,project_id,plan_id,sources[index%len(sources)]['source_id'],index,'video-1',index*request.settings.duration_seconds,clip.model_dump_json()))
                    service.new_attempt(conn,identifier,request.settings)
        with self.storage.connect() as conn:
            attempts=[dict(a) for a in conn.execute("SELECT a.*,c.plan_json,c.source_id,c.position FROM clip_studio_render_attempts a JOIN clip_studio_clips c ON c.clip_id=a.clip_id WHERE c.project_id=? AND a.status='queued' ORDER BY c.position,a.version",(project_id,))
                      if payload['attempt_ids'] is None or a['attempt_id'] in payload['attempt_ids']]
            payload['attempt_ids']=[a['attempt_id'] for a in attempts]
            conn.execute('UPDATE clip_studio_jobs SET request_json=? WHERE job_id=?',(encoded(payload),row['job_id']))
        for index,attempt in enumerate(attempts):
            stage(f'generate:{attempt["position"]+1}',.25+.65*index/max(1,len(attempts)),f'Generating clip {attempt["position"]+1}; previous exports remain immutable')
            directory=service.root/project_id/'renders'/attempt['attempt_id']
            with self.storage.connect() as conn:conn.execute("UPDATE clip_studio_render_attempts SET status='running',error=NULL WHERE attempt_id=?",(attempt['attempt_id'],))
            try:
                clip=PlannedClip.model_validate_json(attempt['plan_json'])
                source=next(s for s in sources if s['source_id']==attempt['source_id'])
                previous,previous_id=None,None
                if request.settings.continuity=='connected' and clip.transition=='continue':
                    with self.storage.connect() as conn:
                        prev=conn.execute('''SELECT a.*,c.plan_json FROM clip_studio_clips c JOIN clip_studio_render_attempts a
                            ON a.attempt_id=c.selected_attempt_id WHERE c.project_id=? AND c.position=? AND a.status='completed' ''',(project_id,attempt['position']-1)).fetchone()
                    if not prev:raise StudioError('The preceding selected clip must complete before this continuation')
                    previous={'clip_path':prev['output_path'],'source_sha256':json.loads(prev['quality_json'])['sha256'],'source_trim':0,'source_duration':json.loads(prev['plan_json'])['duration_seconds']}
                    previous_id=prev['attempt_id']
                audio=audio_track(clip,request.settings,source,cast,directory/'audio')
                chosen=generator or (NativeGenerator(self.storage,attempt['attempt_id'],stage) if request.settings.backend=='native' else
                    (LtxAdapter if request.settings.video_model=='ltx' else WanAdapter)(self.storage,attempt['attempt_id'],stage))
                output,thumbnail,quality=chosen.generate(clip,request.settings,attempt['seed'],directory,
                    references={'primary_source_id':source['source_id'],'source':source,'sources':sources,'cast':cast,
                        'identity_refs':refs,'adapters':[a for character,a in adapters if character in clip.character_ids],'previous':previous,'previous_attempt_id':previous_id,'audio':audio,'position':attempt['position']})
                quality.update(requested_model=attempt['model'],audio_policy=request.settings.narration)
                stage('assemble',.25+.65*(index+1)/max(1,len(attempts)),'Assembled and validated an individual export with thumbnail')
                with self.storage.connect() as conn:
                    conn.execute("UPDATE clip_studio_render_attempts SET status='completed',output_path=?,thumbnail_path=?,quality_json=?,error=NULL WHERE attempt_id=?",(str(output),str(thumbnail),encoded(quality),attempt['attempt_id']))
                    conn.execute('UPDATE clip_studio_clips SET selected_attempt_id=COALESCE(selected_attempt_id,?) WHERE clip_id=?',(attempt['attempt_id'],attempt['clip_id']))
                # Initial real versions enter Review, but no content decision or upload occurs.
                with self.storage.connect() as conn:selected=conn.execute('SELECT selected_attempt_id FROM clip_studio_clips WHERE clip_id=?',(attempt['clip_id'],)).fetchone()[0]
                if selected==attempt['attempt_id']:
                    from .review import send_to_review
                    try:
                        with self.storage.connect() as conn:actor=conn.execute('SELECT actor FROM clip_studio_projects WHERE project_id=?',(project_id,)).fetchone()[0]
                        send_to_review(service,attempt['attempt_id'],actor)
                    except Exception:
                        with self.storage.connect() as conn:self.storage.event(conn,row['job_id'],'review','Export is ready; use Send to review to retry the handoff. No upload was attempted')
            except Exception as error:
                safe=str(error) if isinstance(error,StudioError) else 'Generation or media validation failed. Inspect the saved chunk IDs and runtime before explicitly retrying'
                uncertain=getattr(error,'uncertain',False)
                with self.storage.connect() as conn:
                    unknown=conn.execute("SELECT 1 FROM clip_studio_chunks WHERE attempt_id=? AND status IN ('submitting','processing')",(attempt['attempt_id'],)).fetchone()
                    uncertain=uncertain or bool(unknown)
                    conn.execute('UPDATE clip_studio_render_attempts SET status=?,error=? WHERE attempt_id=?',('uncertain' if uncertain else 'failed',safe,attempt['attempt_id']))
        with self.storage.connect() as conn:
            statuses=[conn.execute('SELECT status FROM clip_studio_render_attempts WHERE attempt_id=?',(a['attempt_id'],)).fetchone()[0] for a in attempts]
            state='completed' if statuses and all(s=='completed' for s in statuses) else 'uncertain' if 'uncertain' in statuses else 'partial' if 'completed' in statuses else 'failed'
            conn.execute("UPDATE clip_studio_jobs SET status=?,stage='finished',progress=1,heartbeat=? WHERE job_id=? AND status='running'",(state,int(time.time()),row['job_id']))
            conn.execute('UPDATE clip_studio_projects SET updated_at=? WHERE project_id=?',(int(time.time()),project_id))
            self.storage.event(conn,row['job_id'],'finished','Real exports are available; selected initial clips enter manual Review. Approval never publishes')

"""Versioned production utilities, same database and existing review workflow."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from app.services.work_queue import WorkQueue
from app.services.cloud_video_renderer import media_sha256,run,validate_clip
from app.services.job_service import JobService
from app.services.content_storage import ContentStorage
from app.models.long_content_plan import parse_content_plan


class ProductionTools:
    def __init__(self,db_path=None):
        self.queue=WorkQueue(db_path);self.db_path=self.queue.db_path
        self.root=Path(os.getenv('PRODUCTION_TOOLS_DIR','data/production_tools')).absolute()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.queue.connect() as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS production_tool_jobs (
                tool_job_id TEXT PRIMARY KEY,actor TEXT NOT NULL,request_id TEXT NOT NULL,
                kind TEXT NOT NULL,request_json TEXT NOT NULL,status TEXT NOT NULL,
                result_json TEXT,error TEXT,created_at INTEGER NOT NULL,UNIQUE(actor,request_id));
                CREATE TABLE IF NOT EXISTS production_tool_candidates (
                candidate_id TEXT PRIMARY KEY,actor TEXT NOT NULL,data_json TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS production_tool_reviews (
                tool_job_id TEXT PRIMARY KEY,job_id TEXT NOT NULL UNIQUE);''')

    def enqueue(self,kind,params,actor,request_id):
        if kind not in ('speech','stock','captions','regenerate','research','illustration'):raise ValueError('Unsupported production tool')
        payload=json.dumps(params,sort_keys=True,allow_nan=False)
        with self.queue.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT * FROM production_tool_jobs WHERE actor=? AND request_id=?',(actor,str(request_id))).fetchone()
            if old:
                if old['kind']!=kind or old['request_json']!=payload:raise ValueError('This request ID was used with different tool settings')
                return self.public(old)
            if conn.execute("SELECT COUNT(*) FROM production_tool_jobs WHERE status IN ('queued','running')").fetchone()[0]>=20:raise ValueError('Production tools queue is full')
            identifier=str(uuid4())
            conn.execute("INSERT INTO production_tool_jobs VALUES (?,?,?,?,?,'queued',NULL,NULL,?)",(identifier,actor,str(request_id),kind,payload,int(time.time())))
        self.queue.enqueue('tool',identifier)
        if os.getenv('DURABLE_WORKERS','false').lower()!='true':self.launch(identifier)
        return {'tool_job_id':identifier,'status':'queued'}

    def launch(self,identifier):
        environment={key:value for key,value in os.environ.items() if key in {'PATH','HOME','LANG','TMPDIR','DATABASE_PATH','PRODUCTION_TOOLS_DIR','SPEECH_RUNTIME_ROOT','PEXELS_API_KEY','CLIP_STUDIO_ANALYSIS_PYTHON','CLIP_STUDIO_WHISPER_DIR','LOCAL_CINEMATIC_ROOT','LOCAL_CINEMATIC_PYTHON','STILL_IMAGE_ROOT','LOG_FORMAT_JSON'}}
        environment['DATABASE_PATH']=self.db_path
        folder=self.root/identifier;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        def failed():
            with self.queue.connect() as conn:
                conn.execute("UPDATE production_tool_jobs SET status='failed',error='Production worker exited before claiming this job. Inspect setup and explicitly start a new version.' WHERE tool_job_id=? AND status='queued'",(identifier,))
                conn.execute("UPDATE app_work_queue SET status='interrupted',error='Worker launch failed; inspect before manual recovery' WHERE kind='tool' AND target_id=? AND status='queued'",(identifier,))
        try:
            log=folder/'application-worker.log'
            with log.open('a') as diagnostics:
                log.chmod(0o600)
                child=subprocess.Popen([sys.executable,'-m','app.workers.application_worker','--drain'],cwd=Path(__file__).absolute().parents[2],env=environment,
                    stdin=subprocess.DEVNULL,stdout=diagnostics,stderr=diagnostics,start_new_session=True)
        except OSError:
            failed();raise ValueError('Could not launch the production worker; inspect saved tool history') from None
        import threading
        def wait():
            child.wait()
            threading.Event().wait(2)
            failed()
        threading.Thread(target=wait,daemon=True).start()

    def public(self,row):
        value=dict(row);value.pop('request_json',None)
        result=json.loads(value.pop('result_json') or 'null')
        if result:
            result={k:v for k,v in result.items() if k!='path'}
            if result.get('asset_type'):result['download_url']='/api/v1/tools/jobs/'+value['tool_job_id']+'/asset'
        value['result']=result;return value

    def recover(self):
        self.queue.recover()
        with self.queue.connect() as conn:
            conn.execute("UPDATE production_tool_jobs SET status='interrupted',error='Worker stopped. Inspect saved outputs before explicitly starting a new version.' WHERE status IN ('queued','running') AND tool_job_id IN (SELECT target_id FROM app_work_queue WHERE kind='tool' AND status='interrupted')")

    def get(self,identifier,actor):
        self.recover()
        with self.queue.connect() as conn:row=conn.execute('SELECT * FROM production_tool_jobs WHERE tool_job_id=? AND actor=?',(identifier,actor)).fetchone()
        if not row:raise ValueError('Production tool job not found')
        return self.public(row)

    def list(self,actor):
        self.recover()
        with self.queue.connect() as conn:return [self.public(r) for r in conn.execute('SELECT * FROM production_tool_jobs WHERE actor=? ORDER BY created_at DESC,rowid DESC LIMIT 100',(actor,))]

    def asset(self,identifier,actor):
        self.get(identifier,actor)
        with self.queue.connect() as conn:row=conn.execute('SELECT result_json FROM production_tool_jobs WHERE tool_job_id=?',(identifier,)).fetchone()
        data=json.loads(row[0] or '{}');path=Path(data.get('path','')).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file() or media_sha256(path)!=data.get('sha256'):raise ValueError('Tool asset is unavailable or changed')
        return path

    def source(self,job_id):
        job=JobService(self.db_path).get_job(job_id)
        if not job or not job.get('video_path') or not Path(job['video_path']).is_file():raise ValueError('Choose a completed existing video job')
        if not job.get('plan_id'):raise ValueError('Source job has no plan')
        plan=parse_content_plan(ContentStorage(self.db_path).get_plan(job['plan_id']))
        return job,plan

    def register_review(self,identifier,path,plan,metadata):
        jobs=JobService(self.db_path);content=ContentStorage(self.db_path,self.root/'plans')
        with self.queue.connect() as conn:
            old=conn.execute('SELECT job_id FROM production_tool_reviews WHERE tool_job_id=?',(identifier,)).fetchone()
        if old:return old[0]
        plan.review_status='draft';plan_id,plan_path=content.save_plan(plan);plan_path.chmod(0o600)
        import datetime
        now=datetime.datetime.now(datetime.timezone.utc).isoformat();job_id=str(uuid4())
        source_job=jobs.get_job(metadata['source_job_id']) if metadata.get('source_job_id') else None
        category=source_job['category'] if source_job else plan.category
        manifest=path.parent/'manifest.json'
        manifest.write_text(json.dumps({'status':'pending_review','factual_status':'unverified','source':metadata,
            'warnings':['Derivative export: inspect source rights, speech, captions and factual claims before approval.']}));manifest.chmod(0o600)
        with self.queue.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',(job_id,category,'US',1,metadata.get('image_mode','text'),'pending_review',plan_id,str(path),None,now,now))
            if metadata.get('legacy_generation_config'):
                from app.services.generation_storage import GenerationStorage
                from app.models.generation_config import GenerationConfig
                GenerationStorage.save_for_job(conn,job_id,GenerationConfig.model_validate(metadata['legacy_generation_config']),legacy_request=False)
            conn.execute('INSERT INTO production_tool_reviews VALUES (?,?)',(identifier,job_id))
        return job_id

    def run(self,identifier):
        with self.queue.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM production_tool_jobs WHERE tool_job_id=?',(identifier,)).fetchone()
            if not row or row['status']!='queued':return
            conn.execute("UPDATE production_tool_jobs SET status='running' WHERE tool_job_id=?",(identifier,))
        folder=self.root/identifier;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        params=json.loads(row['request_json'])
        try:
            if row['kind']=='speech':
                from app.services.tts_service import TTSService
                output=folder/'narration.wav';TTSService(profile_id=params.get('profile_id')).synthesize(params['text'],output)
                result={'path':str(output),'asset_type':'audio','sha256':media_sha256(output),'provider':'piper' if params.get('profile_id') else 'espeak'}
            elif row['kind']=='stock':
                if params.get('rights_confirmed') is not True:raise ValueError('Confirm the stock license and intended-use rights, including for cached assets')
                from app.services.stock_service import StockService
                with self.queue.connect() as conn:candidate=conn.execute('SELECT data_json FROM production_tool_candidates WHERE candidate_id=? AND actor=?',(params['candidate_id'],row['actor'])).fetchone()
                if not candidate:raise ValueError('Stock candidate not found')
                selected=json.loads(candidate[0]);cached=None
                with self.queue.connect() as conn:
                    prior=conn.execute("SELECT result_json FROM production_tool_jobs WHERE actor=? AND kind='stock' AND status='completed' ORDER BY created_at DESC LIMIT 100",(row['actor'],)).fetchall()
                for entry in prior:
                    data=json.loads(entry['result_json'] or '{}');meta=data.get('provenance',{})
                    path=Path(data.get('path','')).resolve()
                    if meta.get('id')==selected['id'] and meta.get('kind')==selected['kind'] and path.is_relative_to(self.root.resolve()) and path.is_file() and media_sha256(path)==data.get('sha256'):
                        cached=data;break
                if cached:
                    result={**cached,'cache_hit':True}
                else:
                    output,metadata=StockService().download(selected,folder,confirmed=params.get('rights_confirmed',False))
                    result={'path':str(output),'asset_type':metadata['kind'],'sha256':metadata['sha256'],'provenance':metadata,'cache_hit':False}
            elif row['kind']=='illustration':
                from app.services.still_images import generate
                output=folder/'illustration.png';metadata=generate(params['text'],output,aspect_ratio=params['aspect_ratio'],seed=params['seed'])
                result={'path':str(output),'asset_type':'image','sha256':metadata['sha256'],'provenance':metadata}
            elif row['kind']=='research':
                from app.services.research_service import research
                result=research(params['query'])
            elif row['kind']=='captions':result=self.caption_export(identifier,params,folder)
            else:result=self.regenerate(identifier,params,folder)
            with self.queue.connect() as conn:conn.execute("UPDATE production_tool_jobs SET status='completed',result_json=? WHERE tool_job_id=?",(json.dumps(result,allow_nan=False),identifier))
        except Exception as error:
            # Only known application ValueErrors are permitted into UI; external
            # SDK/subprocess errors may contain credentials or signed URLs.
            from app.services.cloud_video_renderer import MediaValidationError
            message=str(error) if type(error) is ValueError or isinstance(error,MediaValidationError) else 'Tool execution failed. Check setup and inspect this saved job before retrying manually.'
            with self.queue.connect() as conn:conn.execute("UPDATE production_tool_jobs SET status='failed',error=? WHERE tool_job_id=?",(message,identifier))

    def is_illustrated(self,job,plan):
        config=job.get('generation_config')
        if config:return config.get('video_provider','local')=='local'
        base=Path(job['video_path']).parent
        return all((base/f'scene_{s.scene_number}{suffix}').is_file() for s in plan.scenes for suffix in ('.png','.wav','.mp4'))

    def validate_export(self,path,plan,illustrated):
        if not illustrated:return validate_clip(path,plan.total_duration,require_audio=True)
        from app.services.cloud_video_renderer import probe
        info=probe(path);video=next((s for s in info['streams'] if s['codec_type']=='video'),None)
        duration=float(info['format']['duration'])
        if not video or abs(duration-plan.total_duration)>.6 or not any(s['codec_type']=='audio' for s in info['streams']):raise ValueError('Illustrated export failed its duration/audio checks')
        run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],timeout=max(180,int(duration*3)))
        return {'sha256':media_sha256(path),'duration_seconds':duration,'technical_passed':True,
            'asset_type':'illustrated_video','motion_requirement':'not applicable to the preserved illustrated preview'}

    def caption_export(self,identifier,params,folder):
        from app.services.speech_analysis import analyze_audio,write_word_captions
        from app.services.cloud_video_renderer import probe
        job,plan=self.source(params['job_id']);source=Path(job['video_path'])
        info=probe(source);stream=next(s for s in info['streams'] if s['codec_type']=='video')
        report=analyze_audio(source,folder/'analysis',expected=params.get('expected',''),forced=params['forced'],model=params['model'])
        caption=folder/'captions.ass';write_word_captions(caption,report,stream['width'],stream['height'])
        output=folder/'captioned.mp4'
        try:subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-vf','ass=captions.ass','-c:v','libx264','-preset','fast','-c:a','copy',str(output)],cwd=folder,check=True,capture_output=True,timeout=3600)
        except (OSError,subprocess.SubprocessError):raise ValueError('Captioned export failed; inspect FFmpeg/libass support') from None
        quality=self.validate_export(output,plan,self.is_illustrated(job,plan))
        review=self.register_review(identifier,output,plan,{'source_job_id':job['job_id'],'source_sha256':media_sha256(source),'speech_quality':report,'asset_type':quality.get('asset_type','generated_video')})
        return {'path':str(output),'sha256':quality['sha256'],'asset_type':'video','speech_quality':report,'review_job_id':review}

    def regenerate(self,identifier,params,folder):
        from app.services.image_service import ImageService
        from app.services.tts_service import TTSService
        from app.services.video_renderer import VideoRenderer
        from app.services.cloud_video_renderer import probe
        job,plan=self.source(params['job_id'])
        if (job.get('generation_config') or {}).get('video_provider','local')!='local':raise ValueError('These controls regenerate legacy illustrated videos; use individual shot regeneration for moving models')
        scene=next((s for s in plan.scenes if s.scene_number==params['scene_number']),None)
        if not scene:raise ValueError('Scene does not belong to this video')
        base=Path(job['video_path']).parent
        required=[base/f'scene_{s.scene_number}{suffix}' for s in plan.scenes for suffix in ('.png','.wav','.mp4')]
        if not all(p.is_file() for p in required):raise ValueError('Original illustrated scene assets are unavailable; originals were preserved')
        for path in required:shutil.copyfile(path,folder/path.name)
        info=probe(job['video_path']);stream=next(s for s in info['streams'] if s['codec_type']=='video')
        from fractions import Fraction
        fps=round(float(Fraction(stream.get('avg_frame_rate','30/1'))))
        renderer=VideoRenderer(stream['width'],stream['height'],fps)
        image=folder/f'scene_{scene.scene_number}.png';audio=folder/f'scene_{scene.scene_number}.wav'
        if params['asset']=='image':
            if params.get('text'):scene.visual_prompt=params['text']
            mode=job['image_mode']
            if mode not in ('text','diffusion'):raise ValueError('Unsupported legacy image mode')
            config=job.get('generation_config') or {}
            image_service=ImageService(mode=mode,model_id=config.get('image_model'),visual_style=config.get('visual_style','cinematic'))
            if mode=='diffusion':
                import fcntl
                from app.services.local_cinematic_models import runtime_root
                root=runtime_root();root.mkdir(parents=True,exist_ok=True)
                with (root/'gpu.lock').open('a') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX)
                    image_service.generate(scene.visual_prompt,image,seed=params['seed'])
            else:image_service.generate(scene.visual_prompt,image,seed=params['seed'])
        else:
            if params.get('text'):scene.narration=params['text']
            TTSService(profile_id=params.get('profile_id')).synthesize(scene.narration,audio)
        prepared=folder/f'scene_{scene.scene_number}_caption.png';renderer.prepare_image(image,scene.on_screen_text,prepared)
        renderer.render_scene(prepared,audio,scene.duration_seconds,folder/f'scene_{scene.scene_number}.mp4')
        output=folder/'final_video.mp4';renderer.concatenate([folder/f'scene_{s.scene_number}.mp4' for s in plan.scenes],output)
        quality=self.validate_export(output,plan,True)
        review=self.register_review(identifier,output,plan,{'source_job_id':job['job_id'],'source_sha256':media_sha256(job['video_path']),'regenerated_scene':scene.scene_number,'asset':params['asset'],'asset_type':'illustrated_video','image_mode':job['image_mode'],'legacy_generation_config':job.get('generation_config')})
        return {'path':str(output),'sha256':quality['sha256'],'asset_type':'video','review_job_id':review,'original_job_id':job['job_id']}

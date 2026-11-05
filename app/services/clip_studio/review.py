"""Idempotent import into existing jobs: pending review only, never approval/upload."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from app.models.content_plan import ContentPlan, Scene
from pydantic import Field, model_validator
from typing import Literal

from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.cloud_video_renderer import media_sha256
from .media import StudioError


class ClipReviewPlan(ContentPlan):
    """Imported clips have their real duration; the old 30–60s plan stays unchanged."""
    plan_kind: Literal['clip_studio'] = 'clip_studio'
    scenes: list[Scene] = Field(min_length=1,max_length=5)

    @model_validator(mode='after')
    def validate_scenes(self):
        if [s.scene_number for s in self.scenes]!=list(range(1,len(self.scenes)+1)) or not 2<=self.total_duration<=300:
            raise ValueError('Imported clip must contain ordered scenes totalling 2–300 seconds')
        return self


def send_to_review(service, attempt_id, actor):
    path=service.asset(attempt_id,actor,'video')
    with service.storage.connect() as conn:
        row=conn.execute('''SELECT a.*,c.plan_json,c.project_id FROM clip_studio_render_attempts a
            JOIN clip_studio_clips c ON c.clip_id=a.clip_id WHERE a.attempt_id=?''',(attempt_id,)).fetchone()
        existing=conn.execute('SELECT job_id FROM clip_studio_review_links WHERE attempt_id=?',(attempt_id,)).fetchone()
    if existing:return {'job_id':existing[0],'status':'already_sent'}
    quality=json.loads(row['quality_json']);clip=json.loads(row['plan_json'])
    if quality.get('generator') not in ('native','comfyui') or not quality.get('technical_passed'):
        raise StudioError('Only validated real generation can enter the review queue; mock clips cannot be published')
    if media_sha256(path)!=quality['sha256']:raise StudioError('Clip integrity check failed')
    JobService(service.storage.db_path);ContentStorage(service.storage.db_path,service.root/'review-plans')
    duration=clip['duration_seconds'];scenes=[]
    while duration:
        length=min(60,duration);duration-=length
        scenes.append(Scene(scene_number=len(scenes)+1,duration_seconds=length,narration=' '.join(d['text'] for d in clip.get('dialogue',[])) or clip['title'],visual_prompt=clip['generation_prompt']))
    plan=ClipReviewPlan(topic=clip['title'],title=clip['title'][:100],category='clip_studio',description=clip['style_notes'] or clip['title'],
        hashtags=['#AIGenerated'],hook=clip['title'],target_audience='General',scenes=scenes)
    identifier,plan_id=str(uuid4()),str(uuid4())
    plan_path=service.root/'review-plans'/(plan_id+'.json')
    plan_path.write_text(plan.model_dump_json(indent=2));plan_path.chmod(0o600)
    manifest=path.parent/'manifest.json'
    manifest.write_text(json.dumps({'status':'pending_review','factual_status':'unverified','technical_quality':quality,
        'warnings':['AI-generated transformation. Human source-rights, identity, continuity, dialogue and factual review required.'],
        'clip_studio_project_id':row['project_id'],'clip_studio_attempt_id':attempt_id,'scenes':[]}))
    manifest.chmod(0o600)
    now=datetime.now(timezone.utc).isoformat()
    try:
        with service.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute('SELECT job_id FROM clip_studio_review_links WHERE attempt_id=?',(attempt_id,)).fetchone()
            if existing:
                plan_path.unlink(missing_ok=True)
                return {'job_id':existing[0],'status':'already_sent'}
            conn.execute('INSERT INTO content_plans VALUES (?,?,?,?,?,?,?)',(plan_id,plan.topic,plan.title,plan.category,'draft',str(plan_path),now))
            conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,'clip_studio','US',1,'text','pending_review',plan_id,str(path),None,now,now))
            conn.execute('INSERT INTO clip_studio_review_links VALUES (?,?,?)',(attempt_id,identifier,int(time.time())))
    except Exception:
        plan_path.unlink(missing_ok=True);raise
    return {'job_id':identifier,'status':'pending_review'}

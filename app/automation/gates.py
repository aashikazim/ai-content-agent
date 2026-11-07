"""Fail-closed technical/operational evidence; no providers or human claims."""
import json
import math
from pathlib import Path
import re
from app.automation.idempotency import fingerprint
from app.automation.selector import similar
from app.services.cloud_video_renderer import media_sha256,validate_clip


def screenplay(plan):
    return ' '.join(s.get('narration','') for s in plan.get('scenes',[]))


class GateEvaluator:
    def __init__(self,storage,config,*,media_validator=validate_clip,judge=None):
        self.storage,self.config,self.media_validator,self.judge=storage,config,media_validator,judge

    def evaluate(self,item,job,production):
        reasons=[];configuration=production['configuration'];plan=production['production_plan']['content_plan']
        quality=production.get('quality') or {};path=Path(job.get('video_path') or '')
        if item.get('validation_only'):reasons.append('Private validation output cannot be auto-approved or published')
        if production.get('status') not in {'technical_ready','pending_review'}:reasons.append('Production is not technically ready')
        if job['status'] not in {'awaiting_quality_review','pending_review','approved'}:reasons.append('Job is not eligible for review')
        if configuration.get('video_provider')!='local_cinematic':reasons.append('Daily automation is local-only')
        if not quality.get('technical_passed') or not quality.get('sha256') or not path.is_file():
            return reasons+['Validated final media is missing'],None
        try:
            aspect=configuration.get('aspect_ratio');resolution=configuration.get('resolution')
            dimensions={'720p':{'16:9':(1280,720),'9:16':(720,1280),'1:1':(720,720)},'1080p':{'16:9':(1920,1080),'9:16':(1080,1920),'1:1':(1080,1080)}}[resolution][aspect]
            measured=self.media_validator(path,sum(s['duration_seconds'] for s in plan['scenes']),require_audio=True,
                expected_dimensions=dimensions,expected_fps=configuration['fps'])
            if measured['sha256']!=quality['sha256']:reasons.append('Final media changed after validation')
            if item['kind']=='topic_shorts' and (aspect!='9:16' or measured['duration_seconds']>60.6):reasons.append('Shorts require vertical footage no longer than60seconds')
            manifest=json.loads((path.parent/'manifest.json').read_text())
            if len(manifest.get('scenes',[]))!=len(plan['scenes']):reasons.append('Scene evidence is incomplete')
            for scene,expected in zip(manifest.get('scenes',[]),plan['scenes']):
                speech=scene.get('speech_quality') or {};captions=scene.get('captions') or []
                if expected.get('narration','').strip():
                    wer=speech.get('word_error_rate')
                    if not isinstance(wer,(int,float)) or not math.isfinite(wer) or wer>self.config.wer_max or speech.get('silent'):reasons.append('Speech WER gate failed')
                    if configuration.get('captions') not in {'asr','aligned'} or not captions or not speech.get('words'):reasons.append('Aligned caption evidence is missing')
                    last=0
                    for start,end,text in captions:
                        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (start,end)) or not 0<=last<=start<end<=scene['duration']+.5 or not str(text).strip():
                            reasons.append('Caption timing is invalid');break
                        last=end
                    if not (path.parent/f"captions_{scene['scene_number']}.ass").is_file():reasons.append('Caption artifact is missing')
            # Selected clips and frozen identity assets must still exist unchanged.
            with self.storage.connect() as c:
                selected=[dict(r) for r in c.execute('SELECT * FROM video_shots WHERE production_id=? AND selected=1',(production['production_id'],))]
            if len(selected)!=len(production['production_plan']['shots']):reasons.append('Selected shot count is incomplete')
            for shot in selected:
                q=json.loads(shot.get('media_quality_json') or '{}')
                if shot['status']!='completed' or not q.get('sha256') or media_sha256(shot['clip_path'])!=q['sha256']:reasons.append('Selected shot media changed')
        except (OSError,ValueError,KeyError,TypeError):reasons.append('Technical/caption evidence cannot be verified')
        text=plan['title']+' '+screenplay(plan)
        for rule in self.config.hold_rules:
            if rule.kind=='keyword' and rule.expression.casefold() in text.casefold():reasons.append('Owner keyword hold')
            elif rule.kind=='regex' and re.search(rule.expression,text,re.I):reasons.append('Owner regex hold')
            elif rule.kind=='llm_judge':
                try:allowed=self.judge is not None and self.judge(rule.expression,text) is True
                except Exception:allowed=False
                if not allowed:reasons.append('Owner judge hold or unavailable judge')
        return list(dict.fromkeys(reasons)),quality['sha256']

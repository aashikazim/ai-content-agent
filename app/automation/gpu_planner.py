"""Measured single-host capacity forecasts; seed rates stay explicitly labelled."""
from datetime import date,datetime,timedelta,timezone
import math
from typing import Any
from zoneinfo import ZoneInfo
from app.automation.config import AutomationConfig
from app.automation.idempotency import stable_id
from app.automation.storage import AutomationStorage


class GPUPlanner:
    def __init__(self,storage: AutomationStorage,config: AutomationConfig) -> None:
        self.storage,self.config=storage,config
        now=storage.timestamp()
        with storage.connect() as conn:
            for mode,key in [('text','ltx_t2v'),('image','ltx_i2v')]:
                conn.execute('''INSERT OR IGNORE INTO automation_gpu_measurements
                    (measurement_id,model,mode,video_seconds,neural_seconds,is_seed,created_at,updated_at)
                    VALUES (?,'ltx-2.5',?,1,?,1,?,?)''',(stable_id('gpu_seed',mode),mode,config.gpu.seed_seconds_per_video_second[key],now,now))

    def rate(self,model: str,mode: str) -> tuple[float,str]:
        with self.storage.connect() as conn:
            row=conn.execute('''SELECT SUM(neural_seconds)/SUM(video_seconds) FROM automation_gpu_measurements
                WHERE model=? AND mode=? AND is_seed=0''',(model,mode)).fetchone()
            if row[0] is not None:return row[0],'measured'
            seed=conn.execute('''SELECT neural_seconds/video_seconds FROM automation_gpu_measurements
                WHERE model=? AND mode=? AND is_seed=1''',(model,mode)).fetchone()
        if not seed:raise ValueError('No measured or seed rate for the selected model/mode')
        return seed[0],'seed'

    def forecast(self,day: date,items: list[dict[str,Any]]) -> dict[str,Any]:
        zone=ZoneInfo(self.config.timezone);window=self.config.gpu.nightly_window
        start=datetime.combine(day,datetime.strptime(window.start,'%H:%M').time(),zone)
        end=datetime.combine(day,datetime.strptime(window.end,'%H:%M').time(),zone)
        if end<=start:end+=timedelta(days=1)
        capacity=(end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds()
        selected=[]
        for item in items:
            rate,source=self.rate(item['model'],item['input_mode'])
            selected.append(dict(item,rate=rate,rate_source=source,degradations=[]))
        estimate=lambda:sum(i['duration_s']*i['rate']*1.25+120 for i in selected if not i.get('skipped'))
        for step in self.config.gpu.degradation_order:
            if estimate()<=capacity:break
            if step=='shorten_shorts':
                for item in selected:
                    if item.get('kind','short')!='episode' and item['duration_s']>30:item['duration_s']=30;item['degradations'].append('shortened to 30s')
            else:
                for item in reversed(selected):
                    if estimate()<=capacity:break
                    if item.get('skipped') or (step=='drop_wildcard' and item['category']!='wildcard'):continue
                    item['skipped']=True;item['degradations'].append('does not fit nightly GPU window')
        cursor=start.astimezone(timezone.utc)
        for item in selected:
            seconds=0 if item.get('skipped') else item['duration_s']*item['rate']*1.25+120
            item['estimated_seconds']=round(seconds,2);item['planned_start']=cursor.astimezone(zone).isoformat();cursor+=timedelta(seconds=seconds)
            item['planned_end']=cursor.astimezone(zone).isoformat()
        return {'items':selected,'capacity_seconds':capacity,'estimated_seconds':round(estimate(),2),
            'fits':estimate()<=capacity,'buffer_reuse':'not applicable until episode production is implemented',
            'assumption':'25% headroom plus 120s per job for script/TTS/assembly; forecast is not a timing guarantee'}

    def record(self,job_id: str,model: str,mode: str,duration: float,neural_seconds: float,queue_seconds: float=0,wall_seconds: float | None=None) -> None:
        if not all(math.isfinite(v) for v in (duration,neural_seconds,queue_seconds)) or duration<=0 or neural_seconds<=0 or queue_seconds<0:
            raise ValueError('Measured durations must be finite and positive; queue time nonnegative')
        now=self.storage.timestamp()
        with self.storage.connect() as conn:
            conn.execute('''INSERT INTO automation_gpu_measurements
                (measurement_id,job_id,model,mode,video_seconds,neural_seconds,queue_seconds,wall_seconds,is_seed,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,0,?,?) ON CONFLICT(job_id) DO NOTHING''',
                (stable_id('gpu_job',job_id),job_id,model,mode,duration,neural_seconds,queue_seconds,wall_seconds,now,now))

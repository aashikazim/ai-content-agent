"""Generation-only Director wiring; explicit calls, never starts a scheduler."""
from datetime import datetime,timedelta,timezone
import os
from zoneinfo import ZoneInfo
from app.automation.clock import local_day,utc_now
from app.automation.config import load_settings,ShortsLaneConfig,StoryLaneConfig
from app.automation.events import EventLog
from app.automation.gpu_planner import GPUPlanner
from app.automation.selector import TopicSelector
from app.automation.shorts_service import ShortsService,backup_before_upgrade
from app.automation.series.episodes import EpisodeService
from app.automation.storage import AutomationStorage


def window_remaining(settings,clock):
    now=utc_now(clock).astimezone(ZoneInfo(settings.config.timezone));window=settings.config.gpu.nightly_window
    start=datetime.combine(now.date(),datetime.strptime(window.start,'%H:%M').time(),now.tzinfo)
    end=datetime.combine(now.date(),datetime.strptime(window.end,'%H:%M').time(),now.tzinfo)
    if end<=start:
        if now<end:start-=timedelta(days=1)
        else:end+=timedelta(days=1)
    return max(0,(end.astimezone(timezone.utc)-now.astimezone(timezone.utc)).total_seconds()) if start<=now<end else 0


class DirectorRuntime:
    def __init__(self,db_path,settings=None,*,clock=None,shorts=None,episodes=None):
        self.settings=settings or load_settings();backup_before_upgrade(db_path)
        self.storage=AutomationStorage(db_path,clock=clock);self.shorts=shorts;self.episodes=episodes

    def tick(self,*,launch=True):
        if not self.settings.enabled:return {'status':'disabled','lanes':{}}
        remaining=window_remaining(self.settings,self.storage.clock)
        if not remaining:return {'status':'outside_gpu_window','lanes':{}}
        day=local_day(self.storage.clock,self.settings.config.timezone);result={'status':'generation_only','lanes':{}}
        gpu=GPUPlanner(self.storage,self.settings.config)
        for identifier,cfg in self.settings.config.lanes.items():
            with self.storage.connect() as c:override=c.execute('SELECT enabled FROM lane_settings WHERE lane=?',(identifier,)).fetchone()
            if not cfg.enabled or override and override[0]==0:continue
            try:
                if isinstance(cfg,ShortsLaneConfig):
                    service=self.shorts or ShortsService(self.storage.db_path,self.settings,clock=self.storage.clock)
                    from app.automation.reports import read_day
                    existing=read_day(self.storage.db_path,day,identifier)
                    if existing['run']:result['lanes'][identifier]={'status':'existing','run_id':existing['run']['run_id']};continue
                    preview=TopicSelector(self.storage.db_path,self.storage.clock).preview(cfg)
                    items=[{'slot':i,'category':t['category'],'duration_s':(cfg.recipes.get(t['category']) or cfg.recipes['wildcard']).duration_s,'model':(cfg.recipes.get(t['category']) or cfg.recipes['wildcard']).model,'input_mode':(cfg.recipes.get(t['category']) or cfg.recipes['wildcard']).input_mode} for i,t in enumerate(preview['topics'],1)]
                    if not items or gpu.forecast(day,items)['estimated_seconds']>remaining:result['lanes'][identifier]={'status':'insufficient_remaining_window'};continue
                    run=service.enqueue_day(day,identifier,actor='automation:director',confirmed=True,launch=launch)
                    result['lanes'][identifier]={'status':'queued','run_id':run['run']['run_id']}
                    remaining-=run['run']['forecast']['estimated_seconds']
                elif isinstance(cfg,StoryLaneConfig):
                    estimate=cfg.episode_duration_s.max*gpu.rate(cfg.episode_render.model,'image')[0]*1.25+120
                    if estimate>remaining:result['lanes'][identifier]={'status':'reuse_buffer','reason':'Remaining GPU window cannot fit an episode'};continue
                    service=self.episodes or EpisodeService(self.storage.db_path,self.settings,clock=self.storage.clock)
                    # EpisodeService's own atomic day reservation includes both lanes.
                    result['lanes'][identifier]=service.tick(launch=launch)
                    if result['lanes'][identifier]['queued']:remaining-=estimate
                    if launch and result['lanes'][identifier]['rollovers']:
                        from app.automation.series.service import SeriesService
                        SeriesService(self.storage.db_path,self.settings,clock=self.storage.clock).launch(result['lanes'][identifier]['rollovers'][0])
                else:result['lanes'][identifier]={'status':'reserved_lane_disabled'}
            except ValueError as exc:result['lanes'][identifier]={'status':'held','reason':str(exc)[:500]}
        EventLog(self.storage).append('director','director.tick','day',day.isoformat(),result)
        return result


def database_path():return os.getenv('DATABASE_PATH','data/trends.db')


def collect_tick():
    settings=load_settings()
    if not settings.enabled:return {'status':'disabled'}
    from app.automation.trends.collector import TrendCollector
    from app.automation.trends.sources import sources
    backup_before_upgrade(database_path());storage=AutomationStorage(database_path())
    return TrendCollector(storage,sources(clock=storage.clock),settings.trend_interval_hours).collect(os.getenv('SCHEDULER_REGION','US'))


def generation_tick():return DirectorRuntime(database_path()).tick()


def governor_tick():
    from app.automation.governor import Governor
    settings=load_settings()
    if not settings.enabled or not settings.governor_enabled:return {'status':'disabled'}
    return Governor(database_path(),settings).tick()


def report_tick():
    from app.automation.daily_report import write_daily_report
    settings=load_settings()
    if not settings.enabled:return None
    from app.automation.clock import SystemClock
    return write_daily_report(database_path(),local_day(SystemClock(),settings.config.timezone),settings.config.timezone)


def register_jobs(scheduler,settings):
    """Called by existing scheduler.py only. This never starts it."""
    if not settings.enabled:return []
    common=dict(replace_existing=True,max_instances=1,coalesce=True,misfire_grace_time=300)
    zone=settings.config.timezone
    entries=[('automation_trends',collect_tick,dict(trigger='interval',hours=settings.trend_interval_hours)),
        ('automation_director',generation_tick,dict(trigger='cron',minute='*/10',timezone=zone)),
        ('automation_daily_plan',generation_tick,dict(trigger='cron',hour=settings.plan_hour,minute=settings.plan_minute,timezone=zone)),
        ('automation_report',report_tick,dict(trigger='cron',hour=23,minute=55,timezone=zone))]
    if settings.governor_enabled:entries.append(('automation_governor',governor_tick,dict(trigger='cron',minute='*',timezone=zone)))
    for identifier,callback,trigger in entries:scheduler.add_job(callback,id=identifier,**trigger,**common)
    return [e[0] for e in entries]

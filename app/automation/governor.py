"""Opt-in publication authority. Director and generation workers never import this."""
from datetime import datetime,timedelta,timezone
import fcntl
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from app.automation.clock import local_day,utc_now
from app.automation.config import load_settings
from app.automation.events import EventLog
from app.automation.gates import GateEvaluator,screenplay
from app.automation.idempotency import canonical_json,fingerprint,stable_id
from app.automation.selector import similar
from app.automation.shorts_service import backup_before_upgrade
from app.automation.storage import AutomationStorage
from app.services.cloud_video_service import CloudVideoService
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage


class ApprovedProjection:
    """Read-only dry-run view: never change a real job's approval state."""
    def __init__(self,jobs,identifier):self.jobs,self.identifier=jobs,identifier
    def get_job(self,identifier):
        value=self.jobs.get_job(identifier)
        return dict(value,status='approved') if value and identifier==self.identifier else value


class Governor:
    def __init__(self,db_path,settings=None,*,clock=None,jobs=None,productions=None,publisher_factory=None,gates=None):
        backup_before_upgrade(db_path)
        self.settings=settings or load_settings();self.storage=AutomationStorage(db_path,clock=clock)
        self.events=EventLog(self.storage)
        self.jobs=jobs if jobs is not None else JobService(db_path)
        self.productions=productions if productions is not None else CloudVideoService(db_path)
        self.publications=PublishingStorage(db_path)
        self.publisher_factory=publisher_factory or self._publisher
        self.gates=gates or GateEvaluator(self.storage,self.settings.config.governor)

    def _publisher(self,identifier,dry_run):
        return PublishingService(jobs=ApprovedProjection(self.jobs,identifier) if dry_run else self.jobs,
            content_storage=self.productions.content_storage,publications=self.publications)

    def killed(self):
        if self.settings.kill_switch_file.exists():return True
        with self.storage.connect() as c:
            row=c.execute("SELECT kill_switch FROM governor_state WHERE scope='global'").fetchone()
        return bool(row and row[0])

    def set_state(self,scope,*,kill_switch=None,paused=None,reason='',confirmed=False,actor='owner'):
        if scope not in {'global','youtube','facebook'} or confirmed is not True:raise ValueError('Explicit administrator confirmation is required')
        if kill_switch is not None and (scope!='global' or type(kill_switch) is not bool):raise ValueError('Global kill switch must be a boolean')
        if paused is not None and (scope=='global' or type(paused) is not bool):raise ValueError('Platform pause must be a boolean')
        if scope!='global' and paused is False:
            # Resuming never clears a publication or operation claim.
            with self.storage.connect() as c:
                pending=c.execute("SELECT 1 FROM publications WHERE platform=? AND status IN ('uploading','needs_reconciliation','submitted') LIMIT 1",(scope,)).fetchone()
            if pending:raise ValueError('Reconcile all outstanding platform uploads before resuming')
        now=self.storage.timestamp()
        with self.storage.connect() as c:
            c.execute('INSERT OR IGNORE INTO governor_state(scope,created_at,updated_at) VALUES (?,?,?)',(scope,now,now))
            if kill_switch is not None:c.execute('UPDATE governor_state SET kill_switch=?,reason=?,updated_at=? WHERE scope=?',(int(kill_switch),reason,now,scope))
            if paused is not None:c.execute('UPDATE governor_state SET paused=?,reason=?,consecutive_failures=CASE WHEN ?=0 THEN 0 ELSE consecutive_failures END,updated_at=? WHERE scope=?',(int(paused),reason,int(paused),now,scope))
        self.events.append('user','governor.state_changed','platform',scope,{'actor':actor,'kill_switch':kill_switch,'paused':paused,'reason':reason})
        return self.status()

    def set_lane(self,lane,mode,*,confirmed=False,actor='owner'):
        if lane not in self.settings.config.lanes or mode not in {'manual','auto_with_gates'} or confirmed is not True:raise ValueError('Choose a configured lane and explicitly confirm its approval mode')
        now=self.storage.timestamp()
        with self.storage.connect() as c:c.execute('''INSERT INTO lane_settings(lane,approval_mode,updated_by,created_at,updated_at) VALUES (?,?,?,?,?)
            ON CONFLICT(lane) DO UPDATE SET approval_mode=excluded.approval_mode,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',(lane,mode,actor,now,now))
        self.events.append('user','governor.lane_mode','lane',lane,{'actor':actor,'mode':mode})
        return self.status()

    def effective_lane(self,identifier):
        config=self.settings.config.lanes[identifier]
        with self.storage.connect() as c:row=c.execute('SELECT enabled,approval_mode FROM lane_settings WHERE lane=?',(identifier,)).fetchone()
        return (config.enabled and (row is None or row['enabled']!=0),row['approval_mode'] if row and row['approval_mode'] else config.approval_mode)

    def status(self):
        with self.storage.connect() as c:
            states=[dict(r) for r in c.execute('SELECT * FROM governor_state')]
            actions=[dict(r) for r in c.execute('SELECT * FROM automation_governor_actions ORDER BY created_at DESC LIMIT 100')]
        return {'enabled':self.settings.enabled and self.settings.governor_enabled,'dry_run':self.settings.governor_dry_run,
            'killed':self.killed(),'platforms':states,'lanes':{k:{'enabled':self.effective_lane(k)[0],'approval_mode':self.effective_lane(k)[1]} for k in self.settings.config.lanes},'actions':actions}

    def candidates(self):
        result=[]
        with self.storage.connect() as c:
            for row in c.execute('SELECT * FROM daily_plans WHERE job_id IS NOT NULL AND status NOT IN (\'skipped\',\'failed\',\'needs_reconciliation\')'):
                value=dict(row);result.append(dict(job_id=row['job_id'],lane=row['lane'],kind='topic_shorts',slot=row['slot'],planned_at=row['planned_publish_at'],validation_only=False))
            for row in c.execute('''SELECT e.*,s.lane,r.validation_only FROM series_episodes e JOIN series s ON s.series_id=e.series_id
                LEFT JOIN automation_episode_runs r ON r.series_id=e.series_id AND r.ep_no=e.ep_no
                WHERE e.job_id IS NOT NULL AND e.status NOT IN ('skipped','failed','needs_reconciliation')'''):
                result.append(dict(job_id=row['job_id'],lane=row['lane'],kind='serialized_story',series_id=row['series_id'],ep_no=row['ep_no'],planned_at=row['planned_publish_at'],validation_only=bool(row['validation_only'])))
        from app.automation.reports import data_root
        if Path(self.storage.db_path).is_relative_to(data_root()/'validation'):
            for item in result:item['validation_only']=True
        return result

    def due_slot(self,item):
        now=utc_now(self.storage.clock);zone=ZoneInfo(self.settings.config.timezone)
        if not item.get('planned_at'):return None
        original=datetime.fromisoformat(item['planned_at']).astimezone(zone)
        if now<original.astimezone(timezone.utc):return None
        day=now.astimezone(zone).date()
        slot=datetime.combine(day,original.timetz().replace(tzinfo=None),zone)
        # Late approval misses its slot. It is eligible at the same slot tomorrow.
        delta=(now-slot.astimezone(timezone.utc)).total_seconds()
        return slot.astimezone(timezone.utc) if 0<=delta<=900 else None

    def observe_publications(self):
        """Read reconciliation results, never retry or infer a remote outcome."""
        with self.storage.connect() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM publications WHERE status IN ('needs_reconciliation','uploading','submitted','private')")]
            uncertain=[dict(r) for r in c.execute("SELECT * FROM automation_governor_actions WHERE mode='live' AND status IN ('reserved','needs_reconciliation')")]
        for row in rows:
            if row['status']=='needs_reconciliation':self.trip(row['platform'],'Publication needs reconciliation',uncertain=True)
            if row['platform']=='youtube' and row['status']=='private' and os.getenv('YOUTUBE_PRIVACY_STATUS','private')=='public':
                self.trip('youtube','Requested public visibility remains private; check project audit/visibility in YouTube before resuming',uncertain=True)
        for row in uncertain:
            known=self.publications.get_by_job(row['job_id'],row['platform'])
            if known and known['status'] in {'published','private','uploaded','submitted','processing'} and known.get('platform_video_id'):
                with self.storage.connect() as c:c.execute("UPDATE automation_governor_actions SET status='reconciled',updated_at=? WHERE action_id=?",(self.storage.timestamp(),row['action_id']))
                continue
            self.trip(row['platform'],'Unfinished Governor action requires inspection; no automatic retry',uncertain=True)

    def trip(self,platform,reason,*,uncertain=False,success=False):
        now=self.storage.timestamp()
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO governor_state(scope,created_at,updated_at) VALUES (?,?,?)',(platform,now,now))
            if uncertain:
                old=c.execute('SELECT paused,reason FROM governor_state WHERE scope=?',(platform,)).fetchone()
                if old['paused'] and old['reason']==reason:return
            if success:c.execute('UPDATE governor_state SET consecutive_failures=0,updated_at=? WHERE scope=?',(now,platform))
            else:
                c.execute('UPDATE governor_state SET consecutive_failures=consecutive_failures+1,reason=?,updated_at=? WHERE scope=?',(reason,now,platform))
                c.execute('UPDATE governor_state SET paused=1 WHERE scope=? AND (consecutive_failures>=3 OR ?)',(platform,uncertain))

    def operational_holds(self,item,platform,job,plan):
        reasons=[]
        with self.storage.connect() as c:
            state=c.execute('SELECT paused FROM governor_state WHERE scope=?',(platform,)).fetchone()
            if state and state[0]:reasons.append('Platform is paused')
            if c.execute('SELECT 1 FROM publications WHERE job_id=? AND platform=?',(job['job_id'],platform)).fetchone():reasons.append('Existing upload attempt: use manual reconciliation, never resubmit')
            if item['kind']=='serialized_story' and c.execute("SELECT 1 FROM series_episodes WHERE series_id=? AND ep_no<? AND status NOT IN ('published','skipped')",(item['series_id'],item['ep_no'])).fetchone():reasons.append('Earlier episode has not been published or explicitly skipped')
            cutoff=(utc_now(self.storage.clock)-timedelta(days=self.settings.config.governor.duplicate_window_days)).isoformat()
            rows=c.execute('''SELECT DISTINCT j.plan_id FROM publications p JOIN jobs j ON j.job_id=p.job_id
                WHERE p.status='published' AND p.updated_at>=? AND p.job_id<>?''',(cutoff,job['job_id'])).fetchall()
        for row in rows:
            prior=self.productions.content_storage.get_plan(row[0])
            if hasattr(prior,'model_dump'):prior=prior.model_dump()
            if prior and (similar(plan['title'],prior['title']) or similar(screenplay(plan),screenplay(prior))):reasons.append('Near duplicate of recently published content');break
        return reasons

    def decision(self,item,platform,reasons):
        # Identical holds are logged once a day, not every scheduler minute.
        detail={'platform':platform,'reasons':reasons,'dry_run':self.settings.governor_dry_run}
        key=f"{local_day(self.storage.clock,self.settings.config.timezone)}:{item['job_id']}:{fingerprint(detail)}"
        claim,fresh=self.storage.claim('governor_decision',key,detail)
        if fresh:
            self.events.append('governor','governor.held' if reasons else 'governor.allowed','job',item['job_id'],detail)
            self.storage.complete_claim(claim['claim_token'],detail)
        return dict(job_id=item['job_id'],platform=platform,reasons=reasons,status='held' if reasons else 'eligible')

    def reserve(self,item,platform,slot,digest):
        mode='dry_run' if self.settings.governor_dry_run else 'live';day=slot.astimezone(ZoneInfo(self.settings.config.timezone)).date().isoformat()
        identifier=stable_id('governor_action',item['job_id'],platform,mode);now=self.storage.timestamp()
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if self.killed():return None
            state=c.execute('SELECT paused FROM governor_state WHERE scope=?',(platform,)).fetchone()
            if state and state[0]:return None
            if c.execute('SELECT 1 FROM automation_governor_actions WHERE action_id=?',(identifier,)).fetchone():return None
            count=c.execute("SELECT count(*) FROM automation_governor_actions WHERE platform=? AND mode=? AND publish_day=?",(platform,mode,day)).fetchone()[0]
            # Count manual publications too; use local dates, not UTC string prefixes.
            for row in c.execute('''SELECT p.* FROM publications p WHERE p.platform=? AND NOT EXISTS
                (SELECT 1 FROM automation_governor_actions a WHERE a.job_id=p.job_id AND a.platform=p.platform AND a.mode='live')''',(platform,)):
                if datetime.fromisoformat(row['created_at']).astimezone(ZoneInfo(self.settings.config.timezone)).date().isoformat()==day:count+=1
            if count>=self.settings.config.governor.daily_publish_cap.get(platform,0):return None
            c.execute('''INSERT INTO automation_governor_actions(action_id,job_id,lane,platform,mode,slot_at,publish_day,media_sha256,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,\'reserved\',?,?)''',(identifier,item['job_id'],item['lane'],platform,mode,slot.isoformat(),day,digest,now,now))
        return identifier

    def tick(self):
        # One evaluation across API and the single scheduler, including both
        # platforms' shared approval transition. Publication keys remain the
        # final atomic upload guard even if another process ignores this lock.
        with Path(self.storage.db_path).with_suffix('.governor.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {'status':'busy','decisions':[]}
            return self._tick()

    def _tick(self):
        if not self.settings.enabled or not self.settings.governor_enabled:return {'status':'disabled','decisions':[]}
        self.observe_publications()
        if self.killed():return {'status':'killed','decisions':[]}
        decisions=[]
        for item in self.candidates():
            cfg=self.settings.config.lanes.get(item['lane'])
            if cfg is None or not self.effective_lane(item['lane'])[0]:continue
            if self.effective_lane(item['lane'])[1]!='auto_with_gates':
                decisions.append(self.decision(item,'manual',['Lane remains manual']));continue
            job=self.jobs.get_job(item['job_id'])
            if not job:continue
            with self.storage.connect() as c:row=c.execute('SELECT production_id FROM video_productions WHERE job_id=?',(item['job_id'],)).fetchone()
            if not row:continue
            production=self.productions.storage.get(row[0]);plan=production['production_plan']['content_plan']
            reasons,digest=self.gates.evaluate(item,job,production)
            try:
                from app.services.cinematic_continuity import stale_dependencies
                if stale_dependencies(self.productions.storage,row[0]):reasons.append('Continuation depends on changed footage')
                if production['configuration'].get('cast_package_id'):self.productions.cast.get(production['configuration']['cast_package_id'])
                from app.models.generation_config import GenerationConfig
                self.productions.references.verified(self.productions.all_references(GenerationConfig.model_validate(production['configuration'])))
            except (ValueError,KeyError,OSError):reasons.append('Frozen identity or continuation evidence cannot be verified')
            for platform in cfg.platforms:
                why=list(reasons)
                if platform not in ({'youtube','facebook'} if item['kind']=='topic_shorts' else {'youtube'}):why.append('Platform not supported for this lane')
                why+=self.operational_holds(item,platform,job,plan)
                slot=self.due_slot(item)
                if slot is None:why.append('Waiting for the next configured publish slot')
                decision=self.decision(item,platform,why);decisions.append(decision)
                if why or self.killed():continue
                identifier=self.reserve(item,platform,slot,digest)
                if identifier is None:decision.update(status='held',reasons=['Existing action, paused platform or daily cap']);continue
                dry=self.settings.governor_dry_run
                try:
                    if self.killed():raise ValueError('Kill switch set after reservation; inspect retained action')
                    enabled,mode=self.effective_lane(item['lane'])
                    if not enabled or mode!='auto_with_gates':raise ValueError('Lane authorization changed after gates')
                    # Gate evidence is checked again immediately before mutations.
                    current=self.jobs.get_job(item['job_id'])
                    if current.get('video_path')!=job.get('video_path'):raise ValueError('Media path changed after gates')
                    from app.services.cloud_video_renderer import media_sha256
                    if media_sha256(current['video_path'])!=digest:raise ValueError('Media changed after gates')
                    if not dry:
                        if current['status']=='awaiting_quality_review':self.productions._send_to_review(row[0],automated=True)
                        current=self.jobs.get_job(item['job_id'])
                        if current['status']=='pending_review':
                            from app.services.review_dashboard_service import ReviewDashboardService
                            ReviewDashboardService(self.jobs,self.productions.content_storage).review(item['job_id'],'approved')
                        if self.killed():raise ValueError('Kill switch set before publishing; upload not started')
                    result=self.publisher_factory(item['job_id'],dry).publish(item['job_id'],platform,dry_run=dry)
                    status='dry_run' if dry else 'completed'
                    with self.storage.connect() as c:c.execute('UPDATE automation_governor_actions SET status=?,result_json=?,updated_at=? WHERE action_id=?',(status,canonical_json({k:result.get(k) for k in ('status','publication_id','platform_video_id')}),self.storage.timestamp(),identifier))
                    if not dry:self.trip(platform,'',success=True)
                    decision['status']=status
                    self.events.append('governor','governor.publish_decision','job',item['job_id'],{'action_id':identifier,'platform':platform,'mode':'dry_run' if dry else 'live','status':status})
                except Exception as exc:
                    # Persist no raw remote body, signed URL or credential-bearing exception.
                    with self.storage.connect() as c:c.execute("UPDATE automation_governor_actions SET status='needs_reconciliation',result_json=?,updated_at=? WHERE action_id=?",(canonical_json({'error_type':type(exc).__name__}),self.storage.timestamp(),identifier))
                    self.trip(platform,'Governor action failed; inspect existing upload/approval before recovery',uncertain=not dry)
                    decision.update(status='needs_reconciliation',reasons=[type(exc).__name__])
        return {'status':'dry_run' if self.settings.governor_dry_run else 'live','decisions':decisions}

    def close_action(self,identifier,*,notes,actor,confirmed=False):
        """Owner closes automation intent only; uniqueness survives permanently."""
        if confirmed is not True or len(notes.strip())<10:raise ValueError('Confirm inspection and provide a recovery note')
        with self.storage.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM automation_governor_actions WHERE action_id=?',(identifier,)).fetchone()
            if not row or row['status'] not in {'reserved','needs_reconciliation'}:raise ValueError('Choose an unresolved Governor action')
            publication=self.publications.get_by_job(row['job_id'],row['platform'])
            if publication and publication['status'] in {'uploading','submitted','processing','needs_reconciliation'}:raise ValueError('Reconcile the platform publication using the existing workflow first')
            c.execute("UPDATE automation_governor_actions SET status='owner_closed',updated_at=? WHERE action_id=?",(self.storage.timestamp(),identifier))
        self.events.append('user','governor.action_closed','job',row['job_id'],{'action_id':identifier,'actor':actor,'notes':notes,'automatic_retry_allowed':False})
        return self.status()

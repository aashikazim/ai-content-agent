"""Unattended series upkeep: synthetic frozen cast, series start and stuck skips.

Used only when config/unattended.yaml opts in. The manual cast workflow is
untouched: this builds a normal immutable cast package from locally generated
SDXL portraits of fictional characters, then activates the series through the
existing approve_cast path with an automation actor recorded in the audit log.
Generation only; nothing here approves content or publishes.
"""
import base64
import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.automation.clock import local_day, utc_now
from app.automation.config import StoryLaneConfig, load_settings
from app.automation.events import EventLog
from app.automation.idempotency import stable_id
from app.automation.reports import data_root
from app.automation.series.service import SeriesService, read_series
from app.automation.shorts_service import backup_before_upgrade
from app.automation.storage import AutomationStorage
from app.automation.unattended import active

ACTOR = 'automation:unattended'
FEMALE = ('woman', 'female', 'girl', ' she ', ' her ', 'mother', 'daughter', 'sister', 'queen', 'actress')
DEAD_PLANNING = {'failed', 'needs_reconciliation', 'launch_failed'}


def portrait_prompt(actor: dict[str, Any], visual_style: str) -> str:
    return (f"Cinematic portrait photograph of one fictional character, {actor['name']}: {actor['description']} "
            f"Wearing {actor['wardrobe']} Visual style: {visual_style} "
            'Single person, centered, facing the camera, head and upper body fully visible, plain softly lit '
            'background, sharp focus, natural skin, no text, no watermark, no logo.')[:5000]


def voice_for(actor: dict[str, Any], index: int) -> str:
    text = f" {actor['description']} {actor['voice_notes']} ".casefold()
    female = any(word in text for word in FEMALE)
    return ('en-us+f1', 'en-us+f2')[index % 2] if female else ('en-us+m1', 'en-us+m2')[index % 2]


class AutoCastService:
    def __init__(self, db_path: str | Path, settings: Any = None, *, clock: Any = None,
                 generate: Callable[..., dict] | None = None, references: Any = None, cast: Any = None,
                 series: Any = None, episodes: Any = None) -> None:
        self.settings = settings or load_settings()
        backup_before_upgrade(db_path)
        self.storage = AutomationStorage(db_path, clock=clock)
        self.events = EventLog(self.storage)
        from app.services.work_queue import WorkQueue
        WorkQueue(db_path)  # Ensures the existing durable queue table exists.
        self._generate, self._references, self._cast = generate, references, cast
        self._series, self._episodes = series, episodes

    # ---- lazily constructed collaborators (tests inject fakes) ----
    def series_service(self) -> Any:
        return self._series or SeriesService(self.storage.db_path, self.settings, clock=self.storage.clock)

    def episode_service(self) -> Any:
        from app.automation.series.episodes import EpisodeService
        return self._episodes or EpisodeService(self.storage.db_path, self.settings, clock=self.storage.clock)

    def lanes(self) -> list[tuple[str, StoryLaneConfig]]:
        if active(self.settings) is None:
            return []
        return [(key, lane) for key, lane in self.settings.config.lanes.items()
                if isinstance(lane, StoryLaneConfig) and lane.enabled]

    # ---- scheduling side: cheap, idempotent, called from the Director tick ----
    def tick(self) -> dict[str, Any]:
        result: dict[str, Any] = {'cast_queued': [], 'series_started': [], 'episodes_skipped': []}
        for lane_id, lane in self.lanes():
            with self.storage.connect() as c:
                rows = [dict(r) for r in c.execute('''SELECT s.series_id,s.status,r.status AS planning FROM series s
                    JOIN automation_series_requests r USING(series_id) WHERE s.lane=? ORDER BY s.created_at''', (lane_id,))]
                claims = {r[0]: (r[1], r[2]) for r in c.execute("SELECT request_key,status,updated_at FROM automation_idempotency WHERE scope='auto_cast'")}
                waiting = c.execute('''SELECT 1 FROM automation_series_rollovers o JOIN series s USING(series_id)
                    WHERE s.lane=? AND o.next_series_id IS NULL''', (lane_id,)).fetchone()
            tried = set(claims)
            # A cast build that crashed mid-way is abandoned after six hours.
            recent = (utc_now(self.storage.clock) - timedelta(hours=6)).isoformat()
            building = any(r['series_id'] in claims and claims[r['series_id']][0] == 'reserved'
                and claims[r['series_id']][1] > recent for r in rows if r['status'] == 'awaiting_cast')
            live = next((r for r in rows if r['status'] == 'active'), None)
            if live:
                result['episodes_skipped'] += self.skip_stuck(live['series_id'])
                continue
            ready = [r for r in rows if r['status'] == 'awaiting_cast' and r['planning'] == 'completed' and r['series_id'] not in tried]
            if ready and lane.series_start_mode == 'auto':
                identifier = ready[0]['series_id']
                stamp = int(time.time())
                with self.storage.connect() as c:
                    fresh = c.execute("INSERT INTO app_work_queue(work_id,kind,target_id,status,created_at,updated_at) VALUES (?,'automation',?,'queued',?,?) ON CONFLICT(kind,target_id) DO NOTHING",
                        (str(uuid4()), 'autocast:' + identifier, stamp, stamp)).rowcount
                if fresh:
                    self.events.append('director', 'series.auto_cast_queued', 'series', identifier, {'lane': lane_id})
                    result['cast_queued'].append(identifier)
                continue
            planning = any(r['status'] == 'planning' and r['planning'] not in DEAD_PLANNING for r in rows)
            if planning or waiting or building or lane.series_start_mode != 'auto':
                continue
            # Nothing usable exists: plan one new series (at most one per day).
            day = local_day(self.storage.clock, self.settings.config.timezone)
            try:
                started = self.series_service().enqueue(stable_id('unattended_series', lane_id, day.isoformat()),
                    genre=active(self.settings).series.genre, lane=lane_id, actor='automation:series', confirmed=True)
                result['series_started'].append(started['series_id'])
            except ValueError as exc:
                self.events.append('director', 'series.auto_start_held', 'lane', lane_id, {'reason': str(exc)[:300]})
        return result

    def skip_stuck(self, identifier: str) -> list[int]:
        """Explicit, audited skips so one bad episode cannot stop the story.

        Never resubmits anything. An episode with any YouTube upload attempt is
        left alone: that outcome belongs to reconciliation, not to a skip.
        """
        hours = active(self.settings).series.auto_skip_stuck_after_hours
        now = utc_now(self.storage.clock)
        failed_before = (now - timedelta(hours=hours)).isoformat()
        unpublished_before = (now - timedelta(hours=2 * hours)).isoformat()
        with self.storage.connect() as c:
            rows = [dict(r) for r in c.execute('''SELECT e.ep_no,e.status,e.job_id,e.planned_publish_at,r.status AS run_status,r.updated_at AS run_updated
                FROM series_episodes e LEFT JOIN automation_episode_runs r ON r.series_id=e.series_id AND r.ep_no=e.ep_no
                WHERE e.series_id=? AND e.status NOT IN ('published','skipped') ORDER BY e.ep_no''', (identifier,))]
            attempted = {r[0] for r in c.execute("SELECT job_id FROM publications WHERE platform='youtube'")} \
                if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone() else set()
            paused = c.execute("SELECT 1 FROM governor_state WHERE (scope='youtube' AND paused=1) OR (scope='global' AND kill_switch=1)").fetchone()
        # A paused platform or kill switch is an owner matter, not a bad episode.
        blocked = bool(paused) or self.settings.kill_switch_file.exists()
        skipped = []
        for row in rows[:1]:  # Only the head of the line can block the series.
            if row['job_id'] in attempted:
                break
            reason = None
            if row['run_status'] in {'failed', 'needs_reconciliation', 'launch_failed'} and row['run_updated'] < failed_before:
                reason = f"local generation {row['run_status']} for over {hours}h"
            elif not blocked and row['planned_publish_at'] and row['planned_publish_at'] < unpublished_before:
                reason = f"not publishable {2 * hours}h after its slot"
            if reason:
                self.episode_service().skip(identifier, row['ep_no'], ACTOR,
                    f'Unattended mode: {reason}; continuing the story in order without resubmitting.', confirmed=True)
                skipped.append(row['ep_no'])
        return skipped

    # ---- worker side: runs inside the single durable queue consumer ----
    def process(self, identifier: str) -> None:
        claim, fresh = self.storage.claim('auto_cast', identifier, {'series_id': identifier})
        if not fresh:
            return  # One attempt per series; a failed one is replaced by a new series.
        try:
            package = self.build(identifier)
            self.storage.complete_claim(claim['claim_token'], {'package_id': package['package_id']})
        except Exception as exc:
            self.storage.mark_uncertain(claim['claim_token'])
            self.events.append('director', 'series.auto_cast_failed', 'series', identifier,
                {'error_type': type(exc).__name__, 'reason': str(exc)[:300] if isinstance(exc, ValueError) else ''})

    def build(self, identifier: str) -> dict[str, Any]:
        series = read_series(self.storage.db_path, identifier)
        lane = self.settings.config.lanes.get(series['lane']) if series else None
        if not series or series['status'] != 'awaiting_cast' or series['planning_status'] != 'completed':
            raise ValueError('Series is not waiting for a cast')
        if active(self.settings) is None or not isinstance(lane, StoryLaneConfig) or lane.series_start_mode != 'auto':
            raise ValueError('Automatic cast is not enabled for this lane')
        from app.services.character_reference_service import CharacterReferenceService
        from app.services.cinematic_cast_service import CinematicCastService
        references = self._references or CharacterReferenceService(self.storage.db_path)
        cast = self._cast or CinematicCastService(self.storage.db_path)
        if self._generate is None:
            from app.services.still_images import generate
        else:
            generate = self._generate
        folder = data_root() / 'auto_cast' / identifier
        members, provenance = [], []
        for index, actor in enumerate(series['bible']['characters']):
            seed = int(hashlib.sha256(f"{identifier}:{actor['character_id']}".encode()).hexdigest(), 16) % 2147483647
            output = folder / (actor['character_id'] + '.png')
            made = generate(portrait_prompt(actor, series['bible']['visual_style']), output, aspect_ratio='1:1', seed=seed)
            reference = references.create({**{k: actor[k] for k in ('character_id', 'name', 'description', 'wardrobe', 'voice_notes')},
                'rights_note': 'Synthetic fictional character generated locally (SDXL) for an unattended series; depicts no real person',
                'rights_confirmed': True, 'image_base64': base64.b64encode(Path(output).read_bytes()).decode()})
            members.append({**{k: actor[k] for k in ('character_id', 'name', 'description', 'wardrobe', 'voice_notes')},
                'reference_ids': [reference['reference_id']], 'primary_reference_id': reference['reference_id'],
                'tts_voice': voice_for(actor, index)})
            provenance.append({'character_id': actor['character_id'], 'reference_id': reference['reference_id'],
                'seed': seed, 'sha256': made.get('sha256'), 'model': made.get('model')})
        package = cast.create({'name': ('Auto cast: ' + series['title'])[:100], 'characters': members,
            'location_notes': '; '.join(l['description'] for l in series['bible']['locations'])[:2000], 'confirmed': True})
        self.events.append('director', 'series.auto_cast_generated', 'series', identifier,
            {'package_id': package['package_id'], 'human_inspected': False, 'portraits': provenance})
        start = local_day(self.storage.clock, self.settings.config.timezone) + timedelta(days=1)
        current = read_series(self.storage.db_path, identifier)
        self.series_service().approve_cast(identifier, package['package_id'], current['updated_at'],
            confirmed=True, actor=ACTOR, start_date=start)
        return package

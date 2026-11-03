"""Tracked Gemini productions with explicit, single-use paid authorization.

Generation tasks never approve/publish. Safe recovery retrieves interaction IDs;
unknown submissions are left for human reconciliation, never resubmitted.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan
from app.services.character_reference_service import CharacterReferenceService
from app.services.cloud_video_storage import CloudVideoStorage
from app.services.cloud_video_renderer import CloudVideoRenderer, validate_clip, MediaValidationError
from app.services.content_storage import ContentStorage
from app.services.gemini_video_service import (
    GeminiVideoService, VideoProviderError, LEGACY_FAILURE_MESSAGE, provider_failure_message,
)
from app.services.generation_storage import GenerationStorage
from app.services.job_service import JobService
from app.services.provider_catalog import validate_for_cloud_video


class CloudVideoService:
    def __init__(self, db_path, *, provider=None, renderer_factory=None):
        self.jobs = JobService(db_path)
        self.storage = CloudVideoStorage(db_path)
        self.content_storage = ContentStorage(db_path, os.getenv('CONTENT_PLANS_DIR', 'data/plans'))
        self.references = CharacterReferenceService(db_path)
        self.provider = provider if provider is not None else GeminiVideoService()
        self.renderer_factory = renderer_factory or CloudVideoRenderer
        self.root = Path(os.getenv('CLOUD_VIDEOS_DIR', 'data/videos/cloud')).resolve()

    @staticmethod
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def script(self, identifier):
        with self.storage.connect() as conn:
            row = conn.execute('SELECT * FROM script_generation_attempts WHERE attempt_id = ?', (identifier,)).fetchone()
        if not row or row['status'] != 'completed':
            raise ValueError('Choose a completed script draft')
        config = GenerationConfig.model_validate(json.loads(row['request_json'])['configuration'])
        production = ProductionPlan.model_validate_json(row['result_json']).validate_configuration(config)
        return config, production

    @staticmethod
    def shot_estimate(duration):
        # Published 720p conversion; allow the maximum native shot length plus
        # input/reasoning allowances. This is an estimate, not an invoice cap.
        return Decimal(max(10, duration)) * Decimal('0.10136') + Decimal('0.116736')

    def make_quote(self, request, actor, config, durations):
        validate_for_cloud_video(config)
        refs = self.references.verified(config.character_reference_ids)
        request['references'] = [{'reference_id': ref['reference_id'], 'sha256': ref['sha256']} for ref in refs]
        estimate = sum((self.shot_estimate(duration) for duration in durations), Decimal(0))
        with self.storage.connect() as conn:
            script_id = request.get('script_attempt_id')
            if not script_id:
                script_id = conn.execute('SELECT script_attempt_id FROM video_productions WHERE production_id=?', (request['production_id'],)).fetchone()[0]
            script_cost = conn.execute('SELECT maximum_cost_usd FROM script_generation_attempts WHERE attempt_id=?', (script_id,)).fetchone()
        script_estimate = Decimal(script_cost[0] or '0')
        request['script_estimate_usd'] = str(script_estimate)
        prior = script_estimate
        if request.get('production_id'):
            with self.storage.connect() as conn:
                prior += sum((Decimal(row[0]) for row in conn.execute(
                    "SELECT estimate_usd FROM video_shots WHERE production_id = ? AND status != 'cancelled'", (request['production_id'],))), Decimal(0))
        if prior + estimate > config.max_cost_usd:
            raise ValueError(f'Estimated production spend ${prior + estimate:.4f} exceeds the configured budget ${config.max_cost_usd}; no generation submitted')
        self.provider.check_model()
        identifier, now = str(uuid4()), int(time.time())
        with self.storage.connect() as conn:
            conn.execute('INSERT INTO video_generation_quotes VALUES (?,?,?,?,?,?,NULL)',
                (identifier, actor, json.dumps(request), self.fingerprint(request), str(estimate), now + 600))
        return {'quote_id': identifier, 'estimate_usd': str(estimate), 'prior_reserved_estimate_usd': str(prior),
                'projected_estimate_usd': str(prior + estimate), 'expires_at': now + 600,
                'shot_count': len(durations), 'model': 'gemini-omni-1.1-flash', 'native_resolution': '720p',
                'notice': 'Estimate only, not a guaranteed billing cap. One paid request per listed shot; no automatic retries. Google stores background interactions for up to 55 days. Selected images, script and prompts are sent to Google. Character and voice continuity are not guaranteed. Extension billing/output semantics need manual inspection.',
                'request_summary': {'operation': request['operation'], 'shot_number': request.get('shot_number'), 'instructions': request.get('instructions', ''), 'reference_count': len(refs), 'durations': durations},
                'source_url': 'https://aistudio.google.com/docs/pricing#gemini-omni-1.1-flash'}

    def quote(self, script_attempt_id, actor, reference_ids=None):
        config, production = self.script(script_attempt_id)
        if reference_ids is not None:
            config = config.model_copy(update={'character_reference_ids': reference_ids})
            config = GenerationConfig.model_validate(config)
        self.validate_references(config, production)
        with self.storage.connect() as conn:
            if conn.execute('SELECT 1 FROM video_productions WHERE script_attempt_id = ?', (script_attempt_id,)).fetchone():
                raise ValueError('This script already has a production; inspect its history or regenerate an individual shot')
        request = {'operation': 'create', 'script_attempt_id': script_attempt_id,
                   'configuration': config.model_dump(mode='json'), 'production_plan': production.model_dump(mode='json')}
        return self.make_quote(request, actor, config, [shot.duration_seconds for shot in production.shots])

    def validate_references(self, config, production):
        refs = self.references.verified(config.character_reference_ids)
        known = {character.character_id for character in production.characters}
        if any(ref['character_id'] not in known for ref in refs):
            raise ValueError('Every selected reference must match a character ID in this script')
        if production.content_type in {'short_drama', 'animated_comedy'} and known != {ref['character_id'] for ref in refs}:
            raise ValueError('Upload and select one reference for every fictional character before generating drama/animation')

    def _consume(self, conn, identifier, actor, confirmed):
        if confirmed is not True:
            raise ValueError('Explicit paid video cost and data-transfer confirmation is required')
        quote = conn.execute('SELECT * FROM video_generation_quotes WHERE quote_id = ?', (str(identifier),)).fetchone()
        if not quote or quote['actor'] != actor or quote['consumed_at'] is not None or quote['expires_at'] <= int(time.time()):
            raise ValueError('Video quote is missing, expired, already used or belongs to another administrator')
        request = json.loads(quote['request_json'])
        if self.fingerprint(request) != quote['request_hash']:
            raise ValueError('The saved quote request changed; obtain a new quote')
        config = GenerationConfig.model_validate(request['configuration'])
        validate_for_cloud_video(config)
        refs = self.references.verified(config.character_reference_ids)
        if request['references'] != [{'reference_id': ref['reference_id'], 'sha256': ref['sha256']} for ref in refs]:
            raise ValueError('Reference assets changed; obtain a new quote')
        if conn.execute("SELECT 1 FROM video_shots WHERE status IN ('submitting','processing') OR (status='queued' AND production_id != ?) LIMIT 1", (request.get('production_id', ''),)).fetchone():
            raise ValueError('A video batch is already active; inspect generation history before submitting another')
        if request.get('production_id'):
            prior = Decimal(request.get('script_estimate_usd','0')) + sum((Decimal(row[0]) for row in conn.execute("SELECT estimate_usd FROM video_shots WHERE production_id=? AND status != 'cancelled'", (request['production_id'],))), Decimal(0))
            if prior + Decimal(quote['estimate_usd']) > config.max_cost_usd:
                raise ValueError('The production budget was used by another request; obtain a new quote')
        conn.execute('UPDATE video_generation_quotes SET consumed_at = ? WHERE quote_id = ?', (int(time.time()), str(identifier)))
        return quote, request, config

    @staticmethod
    def insert_shot(conn, production_id, shot, quote_id, operation='generate', version=1,
                    parent=None, instructions='', target_duration=None):
        identifier, now = str(uuid4()), int(time.time())
        duration = target_duration or shot.duration_seconds
        request = {'shot': shot.model_dump(mode='json'), 'instructions': instructions,
                   'target_duration': duration, 'parent_attempt_id': parent}
        conn.execute('''INSERT INTO video_shots
            (attempt_id,production_id,shot_number,scene_number,version,parent_attempt_id,operation,request_json,quote_id,
             status,estimate_usd,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?,?)''',
            (identifier, production_id, shot.shot_number, shot.scene_number, version, parent, operation,
             json.dumps(request), str(quote_id), str(CloudVideoService.shot_estimate(duration)), now, now))
        return identifier

    def create(self, quote_id, actor, confirmed):
        # Existing script content is copied; this path never regenerates/bills a script.
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            quote, request, config = self._consume(conn, quote_id, actor, confirmed)
            if request['operation'] != 'create':
                raise ValueError('This quote authorizes a shot change, not a new production')
            if conn.execute('SELECT 1 FROM video_productions WHERE script_attempt_id = ?', (request['script_attempt_id'],)).fetchone():
                raise ValueError('This script already has a production; inspect existing history')
            production = ProductionPlan.model_validate(request['production_plan']).validate_configuration(config)
            production_id, job_id, plan_id = str(uuid4()), str(uuid4()), str(uuid4())
            now = int(time.time())
            iso = datetime.now(timezone.utc).isoformat()
            # Save the existing plan after authorization validation, before the
            # transaction commits. An unreferenced file after a disk/DB failure
            # is harmless and can never trigger a cloud request.
            plans = Path(os.getenv('CONTENT_PLANS_DIR', 'data/plans')).resolve()
            plans.mkdir(parents=True, exist_ok=True)
            path = plans / (plan_id + '.json')
            path.write_text(production.content_plan.model_dump_json(), encoding='utf-8')
            conn.execute('''INSERT INTO content_plans (plan_id,topic,title,category,status,json_path,created_at)
                            VALUES (?,?,?,?,?,?,?)''',
                         (plan_id, production.content_plan.topic, production.content_plan.title,
                          production.content_plan.category, 'draft', str(path), iso))
            conn.execute('''INSERT INTO jobs (job_id,category,region,video_limit,image_mode,status,plan_id,created_at,updated_at)
                            VALUES (?,?,?,?,?,'queued',?,?,?)''',
                         (job_id, production.content_plan.category, 'US', 5, config.image_mode, plan_id, iso, iso))
            GenerationStorage.save_for_job(conn, job_id, config)
            conn.execute('INSERT INTO video_productions VALUES (?,?,?,?,?,?,NULL,NULL,?,?)',
                (production_id, job_id, request['script_attempt_id'], config.model_dump_json(),
                 production.model_dump_json(), 'queued', now, now))
            for shot in production.shots:
                self.insert_shot(conn, production_id, shot, quote_id)
            self.storage.event(conn, production_id, 'authorized', 'Human authorized the quoted shot batch; script draft reused without a new script request')
        return self.storage.get(production_id)

    def production_id_for_job(self, job_id):
        with self.storage.connect() as conn:
            row = conn.execute('SELECT production_id FROM video_productions WHERE job_id = ?', (job_id,)).fetchone()
        return row[0] if row else None

    def prompt(self, production, config, request, refs):
        shot = request['shot']
        scene = production.content_plan.scenes[shot['scene_number'] - 1]
        characters = [character.model_dump() for character in production.characters if character.character_id in shot['character_ids']]
        reference_text = '\n'.join(f'<IMAGE_REF_{index}> is {ref["character_id"]}: {ref["description"]}; clothing: {ref["wardrobe"]}; voice: {ref["voice_notes"]}.' for index, ref in enumerate(refs))
        return f'''Produce a continuous moving video, approximately EXACTLY {request['target_duration']} seconds, 24fps.
Style: {config.visual_style}. Content label: {production.factual_status}. Scene: {scene.visual_prompt}
Shot action: {shot['action']}. Camera: {shot['camera']}.
Characters: {json.dumps(characters)}
{reference_text}
Preserve each character's appearance, clothing and voice across shots. No title cards, static slides or baked-in captions.
{'Speak exactly: ' + shot['spoken_text'] if config.narration == 'model_audio' else 'Natural scene ambience; no spoken words. Narration will be added locally.'}
For edit, preserve the original composition and timing except the requested changes. For extension return a complete extended candidate, including its parent clip, with total duration {request['target_duration']} seconds.
Requested change: {request.get('instructions', '')}
Do not invent evidence or real-world verification; this is an AI-generated visual production.'''

    def record(self, attempt_id, snapshot):
        usage = snapshot.get('usage', {})
        clean = {}
        for key in ('total_input_tokens', 'total_output_tokens', 'total_thought_tokens', 'total_cached_tokens'):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                clean[key] = value
        modalities = usage.get('output_tokens_by_modality')
        counts = {}
        if isinstance(modalities, list) and modalities:
            for part in modalities:
                if (not isinstance(part, dict) or part.get('modality') not in {'video','text'}
                        or type(part.get('tokens')) is not int or part['tokens'] < 0):
                    counts = {}; break
                counts[part['modality']] = counts.get(part['modality'],0) + part['tokens']
        if counts:
            clean['output_tokens_by_modality'] = counts
        cost = None
        # Interactions thought tokens are separate from output tokens. Require a
        # consistent complete breakdown; never invent a cost when usage is absent.
        if (counts.get('video',0) > 0 and all(key in clean for key in ('total_input_tokens','total_output_tokens','total_thought_tokens'))
                and sum(counts.values()) == clean['total_output_tokens']):
            cost = str((Decimal(clean['total_input_tokens']) * Decimal('1.5') + Decimal(counts['video']) * Decimal('17.5')
                       + Decimal(counts.get('text',0) + clean['total_thought_tokens']) * Decimal('9')) / 1_000_000)
        state = snapshot['status']
        status = 'processing' if state == 'in_progress' else 'download_pending' if state == 'completed' else 'failed' if state in {'failed','cancelled','incomplete'} else 'needs_reconciliation'
        error = None if status in {'processing','download_pending'} else provider_failure_message(snapshot)
        with self.storage.connect() as conn:
            previous = conn.execute('SELECT * FROM video_shots WHERE attempt_id=?', (attempt_id,)).fetchone()
            if not previous:
                raise ValueError('Shot attempt not found')
            saved_usage = json.dumps(clean) if clean else previous['usage_json'] or '{}'
            saved_cost = cost if clean else previous['calculated_cost_usd']
            conn.execute('''UPDATE video_shots SET interaction_id=?,status=?,usage_json=?,calculated_cost_usd=?,error=?,updated_at=? WHERE attempt_id=?''',
                (snapshot['interaction_id'], status, saved_usage, saved_cost, error, int(time.time()), attempt_id))
            if error and (previous['status'] != status or previous['error'] != error):
                self.storage.event(conn, previous['production_id'], 'provider_failure', error, attempt_id)
        return status

    def process(self, identifier, *, submit_queued=False, attempt_ids=None):
        owner, now = str(uuid4()), int(time.time())
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT * FROM video_processing_leases WHERE production_id=?', (identifier,)).fetchone()
            if existing and existing['expires_at'] > now:
                return self.storage.get(identifier)
            conn.execute('INSERT OR REPLACE INTO video_processing_leases VALUES (?,?,?)', (identifier,owner,now + 1800))
        try:
            return self._process(identifier, submit_queued=submit_queued, attempt_ids=attempt_ids)
        finally:
            with self.storage.connect() as conn:
                conn.execute('DELETE FROM video_processing_leases WHERE production_id=? AND owner=?', (identifier,owner))

    def _process(self, identifier, *, submit_queued=False, attempt_ids=None):
        value = self.storage.get(identifier)
        if not value:
            raise ValueError('Video production not found')
        config = GenerationConfig.model_validate(value['configuration'])
        production = ProductionPlan.model_validate(value['production_plan'])
        with self.storage.connect() as conn:
            # A process crash after crossing this boundary must never cause a POST.
            conn.execute("UPDATE video_shots SET status='needs_reconciliation',error='Submission was interrupted. Inspect provider usage; no retry will be sent.' WHERE production_id=? AND status='submitting' AND updated_at < ?", (identifier, int(time.time()) - 300))
            attempts = [dict(row) for row in conn.execute('SELECT * FROM video_shots WHERE production_id=? ORDER BY shot_number,version', (identifier,))]
        if submit_queued and any(item['status'] == 'needs_reconciliation' and not item['interaction_id'] for item in attempts):
            raise ValueError('Reconcile unknown submissions before submitting any queued shots')
        for attempt in attempts:
            status = attempt['status']
            snapshot = None
            try:
                if status == 'queued' and submit_queued and (attempt_ids is None or attempt['attempt_id'] in attempt_ids):
                    refs = self.references.verified(config.character_reference_ids)
                    request = json.loads(attempt['request_json'])
                    parent = self.storage.attempt(attempt['parent_attempt_id']) if attempt['parent_attempt_id'] else None
                    with self.storage.connect() as conn:
                        conn.execute('BEGIN IMMEDIATE')
                        changed = conn.execute("UPDATE video_shots SET status='submitting',updated_at=? WHERE attempt_id=? AND status='queued'", (int(time.time()), attempt['attempt_id']))
                        if changed.rowcount != 1:
                            continue
                        self.storage.event(conn, identifier, 'submission_started', 'One authorized Gemini request; no automatic retry', attempt['attempt_id'])
                    snapshot = self.provider.submit(self.prompt(production, config, request, refs), aspect_ratio=config.aspect_ratio,
                        references=[Path(ref['image_path']).read_bytes() for ref in refs] if attempt['operation'] == 'generate' else (),
                        previous_id=parent['interaction_id'] if parent and attempt['operation'] != 'generate' else None,
                        operation=attempt['operation'])
                elif (status in {'processing', 'download_pending', 'needs_reconciliation', 'download_failed'}
                      or (status == 'failed' and attempt['error'] == LEGACY_FAILURE_MESSAGE)) and attempt['interaction_id']:
                    # Older failures did not retain Google's errors[] codes.
                    # Refresh those existing IDs once, without a generation POST.
                    snapshot = self.provider.retrieve(attempt['interaction_id'])
                else:
                    continue
                status = self.record(attempt['attempt_id'], snapshot)
                if submit_queued and status in {'failed', 'needs_reconciliation'}:
                    # A successful HTTP submission can already contain a terminal
                    # provider failure. Do not spend more of the authorized batch.
                    break
                if status == 'download_pending':
                    path = self.root / identifier / (attempt['attempt_id'] + '.mp4')
                    self.provider.download(snapshot, path)
                    request = json.loads(attempt['request_json'])
                    quality = validate_clip(path, request['target_duration'], require_audio=config.narration == 'model_audio')
                    with self.storage.connect() as conn:
                        conn.execute('BEGIN IMMEDIATE')
                        # Regenerated versions never replace an earlier selected clip automatically.
                        has_selection = conn.execute('SELECT 1 FROM video_shots WHERE production_id=? AND shot_number=? AND selected=1', (identifier, attempt['shot_number'])).fetchone()
                        conn.execute("UPDATE video_shots SET status='completed',clip_path=?,selected=?,media_quality_json=?,error=NULL,updated_at=? WHERE attempt_id=?", (str(path), 0 if has_selection else 1, json.dumps(quality), int(time.time()), attempt['attempt_id']))
                        self.storage.event(conn, identifier, 'clip_validated', json.dumps(quality), attempt['attempt_id'])
            except (VideoProviderError, MediaValidationError, OSError, ValueError) as error:
                # Never store provider raw bodies, URLs, headers or arbitrary exceptions.
                current = self.storage.attempt(attempt['attempt_id'])
                uncertain = isinstance(error, VideoProviderError) and error.uncertain
                state = 'needs_reconciliation' if uncertain or (current['status'] == 'submitting' and not isinstance(error, VideoProviderError)) else 'download_failed' if current['interaction_id'] else 'failed'
                message = str(error) if isinstance(error, (VideoProviderError, MediaValidationError)) else 'Generation asset preparation failed; inspect references and local storage'
                if isinstance(error, MediaValidationError):
                    state = 'validation_failed'
                with self.storage.connect() as conn:
                    conn.execute('UPDATE video_shots SET status=?,error=?,updated_at=? WHERE attempt_id=?', (state, message, int(time.time()), attempt['attempt_id']))
                    self.storage.event(conn, identifier, state, message, attempt['attempt_id'])
                # Do not spend more of the batch after an uncertain submission/failure.
                break
        return self.refresh_state(identifier)

    def refresh_state(self, identifier):
        value = self.storage.get(identifier)
        with self.storage.connect() as conn:
            selected = [dict(row) for row in conn.execute('SELECT * FROM video_shots WHERE production_id=? AND selected=1', (identifier,))]
        outstanding = any(shot['status'] in {'queued','submitting','processing','download_pending'} for shot in value['shots'])
        failed = any(shot['status'] in {'failed','validation_failed','download_failed','needs_reconciliation','cancelled'} for shot in value['shots'] if not any(item['shot_number'] == shot['shot_number'] for item in selected))
        config = GenerationConfig.model_validate(value['configuration'])
        production = ProductionPlan.model_validate(value['production_plan'])
        unselected_candidates = any(shot['status'] == 'completed' and not shot['selected'] and shot['version'] > 1 for shot in value['shots'])
        if unselected_candidates and value['status'] not in {'clips_ready','technical_ready','pending_review'} and not outstanding:
            with self.storage.connect() as conn:
                conn.execute("UPDATE video_productions SET status='candidate_ready',updated_at=? WHERE production_id=?", (int(time.time()), identifier))
            self.jobs.update_job(value['job_id'], 'awaiting_shot_selection')
            return self.storage.get(identifier)
        if len(selected) == len(production.shots) and not outstanding:
            if value['status'] in {'technical_ready','pending_review','approved'}:
                return value
            with self.storage.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT status,updated_at FROM video_productions WHERE production_id=?', (identifier,)).fetchone()
                if row['status'] == 'assembling' and row['updated_at'] > int(time.time()) - 1800:
                    return self.storage.get(identifier)
                conn.execute("UPDATE video_productions SET status='assembling',updated_at=? WHERE production_id=?", (int(time.time()),identifier))
            try:
                directory = self.root / identifier / ('assembly_' + str(uuid4()))
                path, quality = self.renderer_factory(config).assemble(production, {item['shot_number']: item for item in selected}, directory)
                manifest_path = Path(path).parent / 'manifest.json'
                if manifest_path.is_file():
                    manifest = json.loads(manifest_path.read_text())
                    manifest['shots'] = [{key: item[key] for key in ('attempt_id','shot_number','version','interaction_id','trim_start_seconds')} for item in selected]
                    manifest['character_references'] = [self.references.public(ref) for ref in self.references.verified(config.character_reference_ids)]
                    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
                with self.storage.connect() as conn:
                    conn.execute("UPDATE video_productions SET status='technical_ready',quality_json=?,error=NULL,updated_at=? WHERE production_id=?", (json.dumps(quality), int(time.time()), identifier))
                    self.storage.event(conn, identifier, 'assembled', 'Technical checks passed; human continuity/caption/rights inspection required before review')
                self.jobs.update_job(value['job_id'], 'awaiting_quality_review', video_path=str(path))
            except (ValueError, OSError) as error:
                message = str(error) if isinstance(error, MediaValidationError) else 'Final assembly failed; required clips were preserved for local recovery'
                with self.storage.connect() as conn:
                    conn.execute("UPDATE video_productions SET status='assembly_failed',error=?,updated_at=? WHERE production_id=?", (message, int(time.time()), identifier))
                self.jobs.update_job(value['job_id'], 'failed', error=message)
        else:
            state = 'needs_reconciliation' if any(shot['status'] == 'needs_reconciliation' for shot in value['shots']) else 'failed' if failed else 'processing'
            missing = {shot['shot_number']: shot for shot in value['shots']
                       if shot['status'] in {'failed','validation_failed','download_failed','needs_reconciliation','cancelled'}
                       and not any(item['shot_number'] == shot['shot_number'] for item in selected)}
            error = None
            if missing:
                first = missing[min(missing)]
                error = f"Shot {first['shot_number']} (v{first['version']}): {first['error'] or 'No validated video asset is available.'}"
            with self.storage.connect() as conn:
                conn.execute('UPDATE video_productions SET status=?,error=?,updated_at=? WHERE production_id=?', (state, error, int(time.time()), identifier))
            self.jobs.update_job(value['job_id'], state, error=error)
        return self.storage.get(identifier)


    def editable(self, identifier, conn=None):
        value = self.storage.get(identifier)
        if not value:
            raise ValueError('Production not found')
        job = self.jobs.get_job(value['job_id'])
        if job['status'] in {'approved', 'rejected'}:
            raise ValueError('Approved or rejected jobs are locked; create a new script revision and production')
        with self.storage.connect() as read:
            exists = read.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
            if exists and read.execute('SELECT 1 FROM publications WHERE job_id=? LIMIT 1', (value['job_id'],)).fetchone():
                raise ValueError('This job has a publication attempt; generation changes are locked')
        return value

    def quote_change(self, identifier, actor, shot_number, *, operation='generate', instructions='', parent_attempt_id=None, extension_seconds=3):
        value = self.editable(identifier)
        if operation not in {'generate', 'edit', 'extend'} or len(instructions) > 2000:
            raise ValueError('Unsupported shot operation or instructions')
        production = ProductionPlan.model_validate(value['production_plan'])
        shot = next((item for item in production.shots if item.shot_number == shot_number), None)
        if not shot:
            raise ValueError('Shot not found')
        candidates = [item for item in value['shots'] if item['shot_number'] == shot_number]
        parent = next((item for item in candidates if item['attempt_id'] == parent_attempt_id), None) if parent_attempt_id else max(candidates, key=lambda item: item['version'])
        if not parent:
            raise ValueError('Parent shot attempt not found')
        if any(item['status'] in {'needs_reconciliation','submitting','processing','queued'} for item in candidates):
            raise ValueError('Resolve active or uncertain attempts before authorizing a new shot request')
        target = shot.duration_seconds
        if operation != 'generate':
            if parent['status'] != 'completed' or not parent['interaction_id']:
                raise ValueError('Editing/extension requires a completed generated clip and its interaction ID')
            original = self.storage.attempt(parent['attempt_id'])
            parent_duration = json.loads(original['request_json'])['target_duration']
            target = parent_duration
            if operation == 'extend':
                if type(extension_seconds) is not int or not 3 <= extension_seconds <= 10 or target + extension_seconds > 40:
                    raise ValueError('An extension adds 3–10 seconds, up to a 40-second generated candidate')
                target += extension_seconds
        config = GenerationConfig.model_validate(value['configuration'])
        request = {'operation': operation, 'production_id': identifier, 'shot_number': shot_number,
            'configuration': config.model_dump(mode='json'), 'parent_attempt_id': parent['attempt_id'],
            'target_duration': target, 'instructions': instructions}
        return self.make_quote(request, actor, config, [target])

    def change(self, quote_id, actor, confirmed):
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            quote, request, config = self._consume(conn, quote_id, actor, confirmed)
            if request['operation'] == 'create':
                raise ValueError('This quote authorizes a new production')
            value = self.editable(request['production_id'])
            candidates = conn.execute('SELECT * FROM video_shots WHERE production_id=? AND shot_number=? ORDER BY version',
                (value['production_id'], request['shot_number'])).fetchall()
            parent = next((item for item in candidates if item['attempt_id'] == request['parent_attempt_id']), None)
            if not parent or any(item['status'] in {'needs_reconciliation','queued','submitting','processing'} for item in candidates):
                raise ValueError('Shot history changed or needs reconciliation; inspect it before a new quote')
            production = ProductionPlan.model_validate(value['production_plan'])
            shot = next(item for item in production.shots if item.shot_number == request['shot_number'])
            identifier = self.insert_shot(conn, value['production_id'], shot, quote_id, request['operation'],
                max(item['version'] for item in candidates) + 1, parent['attempt_id'], request['instructions'], request['target_duration'])
            conn.execute("UPDATE video_productions SET status='processing',quality_json=NULL,error=NULL,updated_at=? WHERE production_id=?", (int(time.time()), value['production_id']))
            conn.execute("UPDATE jobs SET status='rendering',error=NULL WHERE job_id=?", (value['job_id'],))
            self.storage.event(conn, value['production_id'], 'shot_authorized', 'Human authorized one new candidate; original selected media preserved', identifier)
        return {'production_id': value['production_id'], 'attempt_id': identifier, 'status': 'queued'}

    def select(self, identifier, attempt_id, trim_start_seconds=0):
        value = self.editable(identifier)
        attempt = self.storage.attempt(attempt_id)
        if not attempt or attempt['production_id'] != identifier or attempt['status'] != 'completed':
            raise ValueError('Select a completed clip from this production')
        if any(item['status'] in {'submitting','processing','queued'} for item in value['shots']):
            raise ValueError('Wait for active or queued shots to resolve before selecting a clip')
        request = json.loads(attempt['request_json'])
        duration = request['shot']['duration_seconds']
        if not isinstance(trim_start_seconds, (int,float)) or not 0 <= trim_start_seconds <= request['target_duration'] - duration:
            raise ValueError('Trim window must fit the generated candidate and planned shot duration')
        if not Path(attempt['clip_path']).is_file():
            raise ValueError('Selected clip asset is missing')
        quality = json.loads(attempt['media_quality_json'] or '{}')
        if quality.get('sha256') and hashlib.sha256(Path(attempt['clip_path']).read_bytes()).hexdigest() != quality['sha256']:
            raise ValueError('The clip changed after validation; retrieve the existing interaction again')
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self.editable(identifier)
            conn.execute('UPDATE video_shots SET selected=0 WHERE production_id=? AND shot_number=?', (identifier, attempt['shot_number']))
            conn.execute("UPDATE video_shots SET selected=1,trim_start_seconds=?,continuity_status='pending' WHERE attempt_id=?", (trim_start_seconds, attempt_id))
            conn.execute("UPDATE video_productions SET status='clips_ready',quality_json=NULL WHERE production_id=?", (identifier,))
            conn.execute("UPDATE jobs SET status='rendering' WHERE job_id=?", (value['job_id'],))
            self.storage.event(conn, identifier, 'shot_selected', 'Human selected a clip version and trim window; review invalidated', attempt_id)
        return self.refresh_state(identifier)

    def reconcile(self, identifier, attempt_id, actor, notes, confirmed, remote_id=None, close_attempt=False):
        from app.services.gemini_video_service import interaction_id
        self.editable(identifier)
        if confirmed is not True or not notes.strip() or len(notes) > 500:
            raise ValueError('Inspect provider usage, confirm the outcome and supply reconciliation notes')
        remote_id = interaction_id(remote_id) if remote_id else None
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            attempt = conn.execute('SELECT * FROM video_shots WHERE attempt_id=? AND production_id=?', (attempt_id,identifier)).fetchone()
            if not attempt or attempt['status'] not in {'needs_reconciliation','download_failed'}:
                raise ValueError('This attempt is not awaiting reconciliation')
            if attempt['interaction_id'] and remote_id and attempt['interaction_id'] != remote_id:
                raise ValueError('An existing interaction ID cannot be replaced')
            remote_id = remote_id or attempt['interaction_id']
            conn.execute('UPDATE video_shots SET status=?,interaction_id=?,error=?,updated_at=? WHERE attempt_id=?',
                ('processing' if remote_id and not close_attempt else 'failed', remote_id,
                 None if remote_id and not close_attempt else 'Human closed this attempt without cancelling, refunding or resubmitting the provider request', int(time.time()), attempt_id))
            self.storage.event(conn, identifier, 'human_reconciliation', f'{actor}: {notes}', attempt_id)
        return self.storage.get(identifier)

    def continue_queued(self, identifier, confirmed):
        value = self.editable(identifier)
        if confirmed is not True:
            raise ValueError('Explicit confirmation is required to continue previously authorized unsubmitted shots')
        if any(item['status'] in {'needs_reconciliation','submitting','processing'} for item in value['shots']):
            raise ValueError('Resolve all active/uncertain shots before continuing the queued batch')
        identifiers = [item['attempt_id'] for item in value['shots'] if item['status'] == 'queued']
        if not identifiers:
            raise ValueError('No unsubmitted authorized shots remain')
        return identifiers

    def send_to_review(self, identifier, *, continuity_confirmed, captions_confirmed, rights_confirmed):
        value = self.editable(identifier)
        if not all(item is True for item in (continuity_confirmed,captions_confirmed,rights_confirmed)):
            raise ValueError('Inspect character/voice continuity, caption timing, factual claims and media rights before sending to review')
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute('SELECT status FROM video_productions WHERE production_id=?', (identifier,)).fetchone()
            job = conn.execute('SELECT * FROM jobs WHERE job_id=?', (value['job_id'],)).fetchone()
            if not current or current['status'] != 'technical_ready' or job['status'] != 'awaiting_quality_review':
                raise ValueError('All selected clips and final technical checks must pass before manual review')
            quality = value.get('quality') or {}
            path = Path(job['video_path'] or '')
            if not quality.get('technical_passed') or not path.is_file():
                raise ValueError('Validated final media is missing')
            if quality.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != quality['sha256']:
                raise ValueError('Final video changed after validation; reassemble before review')
            selected = conn.execute('SELECT * FROM video_shots WHERE production_id=? AND selected=1', (identifier,)).fetchall()
            if len(selected) != len(value['production_plan']['shots']) or any(not Path(item['clip_path']).is_file() for item in selected):
                raise ValueError('Required selected shot media is missing')
            for item in selected:
                media_quality = json.loads(item['media_quality_json'] or '{}')
                if media_quality.get('sha256') and hashlib.sha256(Path(item['clip_path']).read_bytes()).hexdigest() != media_quality['sha256']:
                    raise ValueError('A selected shot changed after validation; retrieve or regenerate it before review')
            conn.execute("UPDATE video_shots SET continuity_status='accepted' WHERE production_id=? AND selected=1", (identifier,))
            conn.execute("UPDATE video_productions SET status='pending_review',updated_at=? WHERE production_id=?", (int(time.time()),identifier))
            conn.execute("UPDATE jobs SET status='pending_review',updated_at=? WHERE job_id=? AND status='awaiting_quality_review'", (datetime.now(timezone.utc).isoformat(),value['job_id']))
            self.storage.event(conn, identifier, 'quality_reviewed', 'Human checked selected footage, voices, caption timing, claims and rights; video still requires separate approval and publishing')
        return self.storage.get(identifier)


    def discard_queued(self, identifier, confirmed):
        value = self.editable(identifier)
        if confirmed is not True:
            raise ValueError('Confirm discarding only the never-submitted shots')
        with self.storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self.editable(identifier)
            changed = conn.execute("UPDATE video_shots SET status='cancelled',error='Human discarded a never-submitted reservation; no provider request was sent',updated_at=? WHERE production_id=? AND status='queued'", (int(time.time()),identifier))
            self.storage.event(conn, identifier, 'queued_discarded', f'Human discarded {changed.rowcount} never-submitted shots; submitted interactions remain for reconciliation')
        return self.refresh_state(identifier)

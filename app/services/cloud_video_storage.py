"""Additive cloud production history. Original clips and authorizations are immutable."""
import json
import time
from app.services.generation_storage import GenerationStorage


class CloudVideoStorage(GenerationStorage):
    def initialize(self):
        super().initialize()
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS video_productions (
                    production_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
                    script_attempt_id TEXT NOT NULL, configuration_json TEXT NOT NULL,
                    production_json TEXT NOT NULL, status TEXT NOT NULL,
                    quality_json TEXT, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_generation_quotes (
                    quote_id TEXT PRIMARY KEY, actor TEXT NOT NULL, request_json TEXT NOT NULL,
                    request_hash TEXT NOT NULL, estimate_usd TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, consumed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS video_shots (
                    attempt_id TEXT PRIMARY KEY, production_id TEXT NOT NULL REFERENCES video_productions(production_id),
                    shot_number INTEGER NOT NULL, scene_number INTEGER NOT NULL,
                    version INTEGER NOT NULL, parent_attempt_id TEXT,
                    operation TEXT NOT NULL, request_json TEXT NOT NULL, quote_id TEXT NOT NULL,
                    status TEXT NOT NULL, interaction_id TEXT, clip_path TEXT,
                    selected INTEGER NOT NULL DEFAULT 0, continuity_status TEXT NOT NULL DEFAULT 'pending',
                    usage_json TEXT, calculated_cost_usd TEXT, actual_cost_usd TEXT,
                    estimate_usd TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(production_id,shot_number,version)
                );
                CREATE TABLE IF NOT EXISTS video_processing_leases (
                    production_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_generation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, production_id TEXT NOT NULL,
                    attempt_id TEXT, event TEXT NOT NULL, message TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS character_references (
                    reference_id TEXT PRIMARY KEY, character_id TEXT NOT NULL,
                    name TEXT NOT NULL, description TEXT NOT NULL, wardrobe TEXT NOT NULL,
                    voice_notes TEXT NOT NULL, rights_note TEXT NOT NULL,
                    image_path TEXT NOT NULL, sha256 TEXT NOT NULL, created_at INTEGER NOT NULL
                );
            ''')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(video_shots)')}
            for name, definition in [('trim_start_seconds', 'REAL NOT NULL DEFAULT 0'), ('media_quality_json', 'TEXT')]:
                if name not in columns:
                    conn.execute(f'ALTER TABLE video_shots ADD COLUMN {name} {definition}')

    @staticmethod
    def event(conn, production_id, event, message, attempt_id=None):
        conn.execute('INSERT INTO video_generation_events (production_id,attempt_id,event,message,created_at) VALUES (?,?,?,?,?)',
                     (production_id, attempt_id, event, message, int(time.time())))

    def get(self, identifier):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM video_productions WHERE production_id = ?', (identifier,)).fetchone()
            if not row:
                return None
            shots = conn.execute('SELECT * FROM video_shots WHERE production_id = ? ORDER BY shot_number,version', (identifier,)).fetchall()
            events = conn.execute('SELECT * FROM video_generation_events WHERE production_id = ? ORDER BY event_id DESC LIMIT 100', (identifier,)).fetchall()
        value = dict(row)
        value['configuration'] = json.loads(value.pop('configuration_json'))
        value['production_plan'] = json.loads(value.pop('production_json'))
        value['quality'] = json.loads(value.pop('quality_json') or 'null')
        value['shots'] = []
        for shot in shots:
            item = dict(shot)
            item.pop('request_json')
            item.pop('clip_path')
            item['usage'] = json.loads(item.pop('usage_json') or 'null')
            item['media_quality'] = json.loads(item.pop('media_quality_json') or 'null')
            item['selected'] = bool(item['selected'])
            item['video_url'] = f'/api/v1/generation/videos/{identifier}/shots/{item["attempt_id"]}/video' if shot['clip_path'] else None
            value['shots'].append(item)
        value['events'] = [dict(event) for event in events]
        return value

    def list_productions(self):
        with self.connect() as conn:
            rows = conn.execute('SELECT production_id FROM video_productions ORDER BY created_at DESC,rowid DESC LIMIT 30').fetchall()
        return [self.get(row['production_id']) for row in rows]

    def attempt(self, identifier):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM video_shots WHERE attempt_id = ?', (identifier,)).fetchone()
        return dict(row) if row else None

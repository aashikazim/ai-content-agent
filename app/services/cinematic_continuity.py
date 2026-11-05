"""Local conditioning dependencies, pinned to a selected immutable clip version."""
import json
from pathlib import Path

from app.services.cloud_video_renderer import media_sha256, run, MediaValidationError


def previous_shot(production, shot, config):
    if config.continuity_mode != 'connected' or shot.get('transition') != 'continue':
        return None
    if shot['shot_number'] <= 1:
        raise ValueError('The first shot cannot continue an earlier clip')
    previous = production.shots[shot['shot_number'] - 2]
    if previous.scene_number != shot['scene_number']:
        raise ValueError('Continuation cannot cross a scene boundary')
    return previous.shot_number


def source_dependency(storage, production_id, source_number):
    with storage.connect() as conn:
        source = conn.execute("SELECT * FROM video_shots WHERE production_id=? AND shot_number=? AND selected=1 AND status='completed'",
                              (production_id, source_number)).fetchone()
    if not source:
        return None
    quality = json.loads(source['media_quality_json'] or '{}')
    if not quality.get('sha256') or not Path(source['clip_path']).is_file() or media_sha256(source['clip_path']) != quality['sha256']:
        raise MediaValidationError('Continuation source changed after validation')
    return {'source_attempt_id': source['attempt_id'], 'source_sha256': quality['sha256'],
            'source_trim': source['trim_start_seconds'],
            'source_duration': json.loads(source['request_json'])['shot']['duration_seconds'],
            'clip_path': source['clip_path']}


def prepare_tail(dependency, output_dir):
    """Carry 17 decoded frames (~0.7s). Keep them outside the planned output timeline."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    start = max(0, dependency['source_trim'] + dependency['source_duration'] - 17 / 24)
    run(['ffmpeg', '-y', '-v', 'error', '-ss', str(start), '-i', dependency['clip_path'],
         '-an', '-vf', 'fps=24', '-frames:v', '17', str(output_dir / 'carry_%02d.png')])
    paths = sorted(output_dir.glob('carry_*.png'))
    if len(paths) != 17:
        raise MediaValidationError('Continuation source did not yield the required 17-frame tail')
    for path in paths:
        path.chmod(0o600)
    return [{'path': str(path), 'frame': index, 'sha256': media_sha256(path)} for index, path in enumerate(paths)]


def stale_dependencies(storage, production_id):
    with storage.connect() as conn:
        rows = conn.execute('''SELECT d.*,s.selected,s.status,s.media_quality_json,s.trim_start_seconds
            FROM video_continuity_dependencies d JOIN video_shots a ON a.attempt_id=d.attempt_id
            JOIN video_shots s ON s.attempt_id=d.source_attempt_id
            WHERE a.production_id=? AND a.selected=1''', (production_id,)).fetchall()
    return [row['attempt_id'] for row in rows if not row['selected'] or row['status'] != 'completed'
            or row['source_sha256'] != json.loads(row['media_quality_json'] or '{}').get('sha256')
            or row['source_trim'] != row['trim_start_seconds']]

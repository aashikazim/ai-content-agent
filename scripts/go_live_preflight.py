"""Go-live checks used by scripts/go_live.sh. Never uploads or generates.

check          verify flags/config; print a summary (exit 1 if not ready)
youtube-auth   read-only credential check (exit 1 if it must be renewed)
list-unsent    list upload attempts that failed before reaching the platform
close-unsent   owner-confirmed close of those attempts, then resume platforms
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
load_dotenv(ROOT / '.env')

from app.automation.config import load_settings  # noqa: E402
from app.services.publishing_storage import PublishingStorage  # noqa: E402

DB = os.getenv('DATABASE_PATH', 'data/trends.db')


def check():
    settings = load_settings()
    problems = []
    if not settings.enabled: problems.append('AUTOMATION_ENABLED must be true in .env')
    if not settings.governor_enabled: problems.append('AUTOMATION_GOVERNOR_ENABLED must be true in .env')
    if settings.governor_dry_run: problems.append('AUTOMATION_GOVERNOR_DRY_RUN must be false in .env')
    if os.getenv('DURABLE_WORKERS', 'false').lower() != 'true': problems.append('DURABLE_WORKERS must be true in .env')
    if settings.unattended is None: problems.append('Set AUTOMATION_UNATTENDED_FILE=config/unattended.yaml in .env and enabled: true in that file')
    if problems:
        print('NOT READY:'); [print('  - ' + p) for p in problems]
        return 1
    u = settings.unattended
    shorts = next(l for l in settings.config.lanes.values() if getattr(l, 'kind', '') == 'topic_shorts')
    print(f"Shorts/day: {u.shorts.per_day} -> {', '.join(u.shorts.platforms)} at {', '.join(shorts.publish_slots)}")
    print(f"Series: {'on' if u.series.enabled else 'off'} -> {', '.join(u.series.platforms)} at {u.series.publish_slot}; auto cast: {u.series.auto_cast}")
    print(f"Scripts: {u.scripts.provider} (fallback: {u.scripts.if_unavailable}); global paid budget/day: ${settings.daily_budget_usd}")
    print(f"YouTube privacy: {os.getenv('YOUTUBE_PRIVACY_STATUS', 'private')}; GPU window: {settings.config.gpu.nightly_window.start}-{settings.config.gpu.nightly_window.end} {settings.config.timezone}")
    if u.scripts.internal != 'ollama':
        import sqlite3
        with sqlite3.connect(Path(DB).absolute().as_uri() + '?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT providers_json,max_daily_usd,max_total_usd,consumed_total_usd,expires_at FROM standing_authorizations WHERE status='active'").fetchall()
        usable = [r for r in rows if u.scripts.internal in r['providers_json']]
        if not usable or settings.daily_budget_usd <= 0:
            print('  ! No usable paid authorization/budget: Shorts scripts will use the fallback.')
        for r in usable:
            print(f"  Paid authorization: ${r['max_daily_usd']}/day, ${r['consumed_total_usd']} of ${r['max_total_usd']} used, expires {r['expires_at']}")
    return 0


def youtube_auth():
    try:
        from app.services.youtube_publisher import YouTubePublisher
        YouTubePublisher().get_client().channels().list(part='id', mine=True).execute()
    except Exception as exc:
        print(f'YouTube credentials are not usable ({type(exc).__name__}).')
        return 1
    print('YouTube credentials OK.')
    return 0


def list_unsent():
    rows = PublishingStorage(DB).unsent_attempts()
    for row in rows:
        print(f"  {row['platform']}  publication {row['publication_id']}  job {row['job_id']}  started {row['attempt_started_at']}")
    return 0 if rows else 3


def close_unsent():
    store = PublishingStorage(DB)
    platforms = set()
    for row in store.unsent_attempts():
        store.close_unsent(row['publication_id'], confirmed_not_uploaded=True, actor='owner:go_live',
            notes='Owner confirmed at go-live that this credential-refresh failure uploaded nothing.')
        platforms.add(row['platform'])
        print(f"  closed {row['publication_id']} (no retry; this job will not be uploaded to {row['platform']})")
    from app.automation.governor import Governor
    governor = Governor(DB)
    for platform in sorted(platforms):
        try:
            governor.set_state(platform, paused=False, reason='Resumed at go-live after closing unsent attempts', confirmed=True, actor='owner:go_live')
            print(f'  resumed {platform}')
        except ValueError as exc:
            print(f'  {platform} stays paused: {exc}')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['check', 'youtube-auth', 'list-unsent', 'close-unsent'])
    sys.exit({'check': check, 'youtube-auth': youtube_auth, 'list-unsent': list_unsent, 'close-unsent': close_unsent}[parser.parse_args().command]())

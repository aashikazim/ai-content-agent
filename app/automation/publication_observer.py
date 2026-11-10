"""Read-only publication status checks for unattended operation.

Uploads return ``uploaded``/``submitted``; series order and the duplicate gate
need a verified ``published``. This asks the platform for the status of videos
that already have a remote ID. It never uploads, retries, or guesses: a record
without a remote ID (for example a revoked OAuth token) is left for the owner.
"""
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.automation.clock import utc_now
from app.automation.config import load_settings
from app.automation.events import EventLog
from app.automation.shorts_service import backup_before_upgrade
from app.automation.storage import AutomationStorage
from app.services.publishing_storage import PublishingStorage

OPEN = ('uploaded', 'submitted', 'processing')


def facebook_status(current: str, body: dict[str, Any]) -> str:
    """Conservative mapping of Meta's video ``status`` field."""
    status = body.get('status') or {}
    if status.get('video_status') == 'error':
        return 'failed'
    publishing = status.get('publishing_phase') or {}
    if status.get('video_status') == 'ready' and publishing.get('status') == 'complete':
        return 'published'
    return current


class PublicationObserver:
    def __init__(self, db_path: str | Path, settings: Any = None, *, clock: Any = None,
                 youtube: Any = None, facebook: Any = None, max_age_days: int = 7) -> None:
        self.settings = settings or load_settings()
        backup_before_upgrade(db_path)
        self.storage = AutomationStorage(db_path, clock=clock)
        self.publications = PublishingStorage(db_path)
        self.youtube, self.facebook, self.max_age_days = youtube, facebook, max_age_days

    def client(self, platform: str) -> Any:
        if platform == 'youtube':
            if self.youtube is None:
                from app.services.youtube_publisher import YouTubePublisher
                self.youtube = YouTubePublisher()
            return self.youtube
        if self.facebook is None:
            from app.services.facebook_publisher import FacebookPublisher
            self.facebook = FacebookPublisher()
        return self.facebook

    def tick(self) -> dict[str, Any]:
        if self.settings.kill_switch_file.exists():
            return {'status': 'killed', 'checked': 0}
        now = utc_now(self.storage.clock)
        cutoff = (now - timedelta(days=self.max_age_days)).isoformat()
        records = [r for r in self.publications.list_publications(statuses=list(OPEN))
                   if r.get('platform_video_id') and r['created_at'] >= cutoff]
        result: dict[str, Any] = {'status': 'checked', 'checked': 0, 'changed': 0, 'unavailable': []}
        for record in records:
            platform = record['platform']
            if platform in result['unavailable']:
                continue
            try:
                if platform == 'youtube':
                    value = self.client(platform).get_video_status(record['platform_video_id'])
                    status, external = value['status'], value['external_status']
                    if status == 'unknown':
                        status = record['status']  # Not visible is not proof of failure.
                else:
                    body = self.client(platform).get_status(record['platform_video_id'])
                    status, external = facebook_status(record['status'], body), str(body.get('status', {}))[:500]
            except Exception as exc:
                # Credentials/network: stop asking this platform until the next tick.
                result['unavailable'].append(platform)
                EventLog(self.storage).append('governor', 'publication.check_unavailable', 'platform', platform,
                    {'error_type': type(exc).__name__, 'owner_action': 'Check platform credentials (YouTube: python scripts/youtube_auth.py)'})
                continue
            stamp = now.isoformat()
            with self.publications.connect() as conn:
                conn.execute('''UPDATE publications SET status=?,external_status=?,last_checked_at=?,updated_at=?
                    WHERE publication_id=? AND status=?''', (status, external, stamp, stamp, record['publication_id'], record['status']))
            result['checked'] += 1
            if status != record['status']:
                result['changed'] += 1
                self.publications.record_event(record['publication_id'], 'status_checked', f'{record["status"]}->{status}')
        return result

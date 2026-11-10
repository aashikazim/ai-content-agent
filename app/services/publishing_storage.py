
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone


class PublishingStorage:
    def __init__(self, db_path="data/trends.db"):
        self.db_path = str(db_path)
        self.initialize()

    def connect(self):
        conn = sqlite3.connect(
            self.db_path,
            timeout=30
        )
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publications (
                    publication_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    platform_video_id TEXT,
                    url TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, platform)
                )
            """)

            # Day 6: Migrate existing publications table
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(publications)"
                )
            }

            new_columns = {
                "attempt_started_at": "TEXT",
                "external_status": "TEXT",
                "last_checked_at": "TEXT",
            }

            for column, column_type in new_columns.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE publications "
                        f"ADD COLUMN {column} {column_type}"
                    )

            # Day 6: Publication event history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publication_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)


    def get_by_job(self, job_id, platform):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT * FROM publications
                WHERE job_id = ? AND platform = ?
            """, (job_id, platform)).fetchone()

        return dict(row) if row else None

    def create(self, job_id, platform):
        publication_id = str(uuid.uuid4())
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO publications (
                    publication_id, job_id, platform,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?)
            """, (
                publication_id,
                job_id,
                platform,
                now,
                now
            ))

        return publication_id

    def update(
        self,
        publication_id,
        status,
        platform_video_id=None,
        url=None,
        error=None
    ):
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                UPDATE publications
                SET status = ?,
                    platform_video_id = COALESCE(
                        ?, platform_video_id
                    ),
                    url = COALESCE(?, url),
                    error = ?,
                    updated_at = ?
                WHERE publication_id = ?
            """, (
                status,
                platform_video_id,
                url,
                error,
                now,
                publication_id
            ))

    def get(self, publication_id):
        with self.connect() as conn:
            row = conn.execute("""
                SELECT * FROM publications
                WHERE publication_id = ?
            """, (publication_id,)).fetchone()

        return dict(row) if row else None




    def record_event(
        self,
        publication_id,
        event_type,
        details=None
    ):
        from datetime import datetime, timezone

        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO publication_events (
                    publication_id,
                    event_type,
                    details,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                publication_id,
                event_type,
                details,
                now
            ))

    def list_publications(self, statuses=None):
        with self.connect() as conn:
            if statuses:
                placeholders = ",".join(
                    "?" for _ in statuses
                )

                rows = conn.execute(
                    f"""
                    SELECT * FROM publications
                    WHERE status IN ({placeholders})
                    ORDER BY created_at DESC
                    """,
                    tuple(statuses)
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM publications
                    ORDER BY created_at DESC
                """).fetchall()

        return [dict(row) for row in rows]

    def reserve_publication(
        self,
        job_id,
        platform
    ):
        """
        Atomically reserve one publication attempt.

        A duplicate reservation raises an SQLite
        integrity error. No second upload is started.
        """
        from datetime import datetime, timezone
        import uuid

        publication_id = str(uuid.uuid4())
        now = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:
            conn.execute("""
                INSERT INTO publications (
                    publication_id,
                    job_id,
                    platform,
                    status,
                    created_at,
                    updated_at,
                    attempt_started_at
                )
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """, (
                publication_id,
                job_id,
                platform,
                now,
                now,
                now
            ))

        return publication_id

    def authorize_retry(self, publication_id, *, confirmed_not_uploaded=False, notes="", actor="local-admin"):
        """Record a human's inspection; never contact a publisher.

        Only a Facebook attempt without a saved remote ID can be released.
        A separate manual publish action must consume this authorization.
        """
        if confirmed_not_uploaded is not True or not isinstance(notes, str) or not notes.strip() or len(notes) > 1000:
            raise ValueError("Confirm that no Facebook video was uploaded and provide inspection notes (1–1000 characters)")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM publications WHERE publication_id=?', (publication_id,)).fetchone()
            if not row:
                raise ValueError('Publication not found')
            if row['platform'] != 'facebook' or row['status'] != 'needs_reconciliation' or row['platform_video_id']:
                raise ValueError('Retry authorization requires a Facebook attempt needing reconciliation with no remote video ID; inspect remote state instead')
            # Preserve the previous failure before a new attempt overwrites the
            # current status/error. Do not copy signed URLs or auth headers.
            error = re.sub(r'https?://\S+', '[URL redacted]', row['error'] or '')
            error = re.sub(r'(?i)\b(?:Bearer|OAuth)\s+\S+', '[authorization redacted]', error)
            details = json.dumps({'actor': actor, 'notes': notes.strip(), 'previous_status': row['status'],
                'previous_error': error[:1000], 'previous_attempt_started_at': row['attempt_started_at']})
            conn.execute("UPDATE publications SET status='retry_authorized',updated_at=? WHERE publication_id=?", (now, publication_id))
            conn.execute('INSERT INTO publication_events (publication_id,event_type,details,created_at) VALUES (?,?,?,?)',
                (publication_id, 'retry_authorized', details, now))
        return self.get(publication_id)

    # Failures raised while obtaining credentials, before any upload request.
    UNSENT_ERRORS = frozenset({"RefreshError"})

    def unsent_attempts(self):
        """Uncertain rows whose recorded failure happened before any upload request."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT p.* FROM publications p WHERE p.status='needs_reconciliation'
                AND COALESCE(p.platform_video_id,'')='' ORDER BY p.created_at""").fetchall()
            result = []
            for row in rows:
                last = conn.execute("""SELECT event_type,details FROM publication_events WHERE publication_id=?
                    ORDER BY event_id DESC LIMIT 1""", (row['publication_id'],)).fetchone()
                if last and last['event_type'] == 'upload_outcome_uncertain' and last['details'] in self.UNSENT_ERRORS:
                    result.append(dict(row))
        return result

    def close_unsent(self, publication_id, *, confirmed_not_uploaded=False, notes="", actor="local-admin"):
        """Owner closes an attempt that never reached the platform. Never a retry.

        Only a credential-refresh failure with no remote ID qualifies. The row and
        its (job_id, platform) uniqueness stay, so this job can never be uploaded
        to that platform again; the status becomes the terminal ``failed``.
        """
        if confirmed_not_uploaded is not True or not isinstance(notes, str) or len(notes.strip()) < 10 or len(notes) > 1000:
            raise ValueError("Confirm that no video was uploaded and provide inspection notes (10–1000 characters)")
        if publication_id not in {row['publication_id'] for row in self.unsent_attempts()}:
            raise ValueError('Only an attempt that failed while refreshing credentials, with no remote video ID, can be closed; inspect remote state instead')
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed = conn.execute("""UPDATE publications SET status='failed',updated_at=? WHERE publication_id=?
                AND status='needs_reconciliation' AND COALESCE(platform_video_id,'')=''""", (now, publication_id))
            if changed.rowcount != 1:
                raise ValueError('Publication changed; inspect it again')
            conn.execute('INSERT INTO publication_events (publication_id,event_type,details,created_at) VALUES (?,?,?,?)',
                (publication_id, 'closed_unsent', json.dumps({'actor': actor, 'notes': notes.strip(), 'retry_allowed': False}), now))
        return self.get(publication_id)

    def reserve_authorized_retry(self, publication_id):
        """Atomically consume a human authorization once, preserving uniqueness."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed = conn.execute("""UPDATE publications SET status='pending',error=NULL,
                attempt_started_at=?,updated_at=?,external_status=NULL,last_checked_at=NULL
                WHERE publication_id=? AND platform='facebook' AND status='retry_authorized'
                AND platform_video_id IS NULL""", (now, now, publication_id))
            if changed.rowcount != 1:
                raise ValueError('Retry authorization is missing or already consumed; duplicate upload prevented')
            conn.execute('INSERT INTO publication_events (publication_id,event_type,details,created_at) VALUES (?,?,?,?)',
                (publication_id, 'retry_reserved', 'Manual publish action consumed one retry authorization', now))
        return publication_id

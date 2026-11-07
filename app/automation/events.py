"""Append-only operational audit records; never store credentials/provider bodies."""

from dataclasses import dataclass
import json
import re
from typing import Any, Literal

from app.automation.idempotency import canonical_json
from app.automation.storage import AutomationStorage


@dataclass(frozen=True)
class AutomationEvent:
    event_id: int
    actor: str
    action: str
    subject_type: str
    subject_id: str
    detail: dict[str, Any]
    timestamp: str


class EventLog:
    def __init__(self, storage: AutomationStorage) -> None:
        self.storage = storage

    def append(
        self, actor: Literal["director", "governor", "user"], action: str,
        subject_type: str, subject_id: str, detail: dict[str, Any] | None = None,
    ) -> int:
        for value in (action, subject_type):
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", value):
                raise ValueError("Event action/type must be bounded identifiers")
        if not subject_id or len(subject_id) > 200:
            raise ValueError("Event subject ID must be bounded and nonempty")
        encoded = canonical_json(detail if detail is not None else {})
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("Event details exceed 16 KiB")
        now = self.storage.timestamp()
        with self.storage.connect() as conn:
            cursor = conn.execute("""INSERT INTO automation_events
                (actor,action,subject_type,subject_id,detail_json,timestamp,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""", (actor, action, subject_type, subject_id, encoded, now, now, now))
            return int(cursor.lastrowid)

    def list(self, *, after_id: int = 0, limit: int = 100) -> list[AutomationEvent]:
        if type(limit) is not int or not 1 <= limit <= 1000 or after_id < 0:
            raise ValueError("Invalid event pagination")
        with self.storage.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_events WHERE event_id>? ORDER BY event_id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [AutomationEvent(
            event_id=row["event_id"], actor=row["actor"], action=row["action"],
            subject_type=row["subject_type"], subject_id=row["subject_id"],
            detail=json.loads(row["detail_json"]), timestamp=row["timestamp"],
        ) for row in rows]

"""Explicit, additive storage in DATABASE_PATH, using the existing SQLite pattern.

Construct only at an explicit entry point. Phase 1 verifies this on database copies;
no import/API startup initializes these tables in the live database.
"""

from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from app.automation.clock import Clock, SystemClock, utc_now
from app.automation.idempotency import canonical_json, daily_slot_id, fingerprint
from app.automation.schema import SCHEMA_STATEMENTS


class IdempotencyConflict(ValueError):
    """A previously reserved operation cannot change payload or repeat execution."""


class AutomationStorage:
    def __init__(self, db_path: str | Path, *, clock: Clock | None = None) -> None:
        self.db_path = str(Path(db_path).absolute())
        self.clock = clock if clock is not None else SystemClock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def timestamp(self) -> str:
        return utc_now(self.clock).isoformat(timespec="microseconds")

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)

    def reserve_slot(
        self, plan_date: date, lane: str, slot: int, recipe: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Reserve a plan item once; never create a job or overwrite a saved recipe."""
        identifier = daily_slot_id(plan_date, lane, slot)
        encoded = canonical_json(recipe)
        now = self.timestamp()
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO daily_plans(daily_plan_id, plan_date, lane, slot, recipe, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(plan_date,lane,slot) DO NOTHING
            """, (identifier, plan_date.isoformat(), lane, slot, encoded, now, now))
            row = conn.execute(
                "SELECT * FROM daily_plans WHERE plan_date=? AND lane=? AND slot=?",
                (plan_date.isoformat(), lane, slot),
            ).fetchone()
            if row["recipe"] != encoded:
                raise IdempotencyConflict("A reserved daily slot has different settings")
            return dict(row), cursor.rowcount == 1

    def claim(
        self, scope: str, request_key: str, request: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Only the winner may submit. An existing reservation is never reclaimed.

        The token binds a later completion to its owner. A crash between external
        submission and completion is uncertain, even if no external ID was saved.
        Future dispatchers must reconcile it, never retry on elapsed time alone.
        """
        if not scope or not request_key or len(scope) > 100 or len(request_key) > 500:
            raise ValueError("Provide bounded nonempty idempotency scope and key")
        digest, now, token = fingerprint(request), self.timestamp(), str(uuid4())
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO automation_idempotency(
                    scope,request_key,request_sha256,claim_token,created_at,updated_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(scope,request_key) DO NOTHING
            """, (scope, request_key, digest, token, now, now))
            row = conn.execute(
                "SELECT * FROM automation_idempotency WHERE scope=? AND request_key=?",
                (scope, request_key),
            ).fetchone()
            if row["request_sha256"] != digest:
                raise IdempotencyConflict("Request key was already used for different settings")
            # Do not give a later caller the winner's completion token.
            result = dict(row)
            if cursor.rowcount != 1:
                result.pop("claim_token")
            return result, cursor.rowcount == 1

    def complete_claim(self, token: str, result: dict[str, Any]) -> None:
        encoded, now = canonical_json(result), self.timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,result_json FROM automation_idempotency WHERE claim_token=?", (token,)
            ).fetchone()
            if row is None:
                raise IdempotencyConflict("Unknown claim owner")
            if row["status"] == "completed" and row["result_json"] == encoded:
                return
            if row["status"] != "reserved":
                raise IdempotencyConflict("Claim is completed or requires reconciliation")
            conn.execute("""UPDATE automation_idempotency
                SET status='completed',result_json=?,updated_at=? WHERE claim_token=?""",
                (encoded, now, token))

    def mark_uncertain(self, token: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute("""UPDATE automation_idempotency
                SET status='needs_reconciliation',updated_at=?
                WHERE claim_token=? AND status='reserved'""", (self.timestamp(), token))
            if cursor.rowcount != 1:
                raise IdempotencyConflict("Only a reserved operation can become uncertain")

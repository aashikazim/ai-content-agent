"""Back up SQLite read-only and verify Phase 1 migrations on a private copy.

Never initializes or writes the source database. Output directory must be new.
"""

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).absolute().parents[1]))

from app.automation.schema import AUTOMATION_TABLES
from app.automation.storage import AutomationStorage

logger = logging.getLogger(__name__)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def snapshot(conn: sqlite3.Connection, tables: list[str]) -> dict[str, Any]:
    """Compare all pre-existing rows without logging their private values."""
    result = {}
    for table in tables:
        hashes = []
        for row in conn.execute(f"SELECT * FROM {quote_identifier(table)}"):
            hashes.append(hashlib.sha256(repr(tuple(row)).encode("utf-8")).digest())
        result[table] = {
            "row_count": len(hashes),
            "rows_sha256": hashlib.sha256(b"".join(sorted(hashes))).hexdigest(),
            "schema": conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0],
        }
    return result


def verify_copy(source: Path, output_dir: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    output_dir = output_dir.absolute()
    # A new private directory prevents overwriting previous audit evidence.
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup_path = output_dir / "trends_backup.db"
    copy_path = output_dir / "trends_migration.db"
    # SQLite's backup API includes committed WAL contents, unlike shutil.copyfile.
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as live:
        with sqlite3.connect(backup_path) as backup:
            live.backup(backup)
    with sqlite3.connect(backup_path) as backup:
        with sqlite3.connect(copy_path) as working:
            backup.backup(working)
    backup_path.chmod(0o600)
    copy_path.chmod(0o600)
    with sqlite3.connect(copy_path) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = snapshot(conn, tables)
        schema_before = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        version_before = conn.execute("PRAGMA user_version").fetchone()[0]
    storage = AutomationStorage(copy_path)
    storage.initialize()
    AutomationStorage(copy_path)
    with sqlite3.connect(copy_path) as conn:
        after = snapshot(conn, tables)
        objects_after = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if before != after or not set(schema_before) <= set(objects_after):
            raise AssertionError("Migration changed pre-existing rows or schema")
        if not AUTOMATION_TABLES <= present or integrity != "ok":
            raise AssertionError("Migration tables or integrity check failed")
        if conn.execute("PRAGMA user_version").fetchone()[0] != version_before:
            raise AssertionError("Migration changed the existing schema version")
    report = {
        "source_open_mode": "read-only", "source_migrated": False,
        "backup_path": str(backup_path), "migration_copy_path": str(copy_path),
        "existing_tables_checked": len(tables),
        "existing_rows_checked": sum(item["row_count"] for item in before.values()),
        "existing_schema_and_rows_preserved": True, "initializations": 3,
        "automation_tables_present": sorted(AUTOMATION_TABLES), "integrity_check": integrity,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/trends.db"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = verify_copy(args.source, args.output_dir)
    logger.info("Migration copy verified: %s", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()

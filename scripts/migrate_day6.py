
import os
import sqlite3
from dotenv import load_dotenv

load_dotenv(".env")

DB_PATH = os.getenv(
    "DATABASE_PATH",
    "data/trends.db"
)


def migrate():
    with sqlite3.connect(DB_PATH) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(publications)"
            )
        }

        additions = {
            "attempt_started_at": "TEXT",
            "external_status": "TEXT",
            "last_checked_at": "TEXT",
        }

        for column, column_type in additions.items():
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE publications "
                    f"ADD COLUMN {column} {column_type}"
                )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            publication_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                publication_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )
        """)

    print("Day 6 migration complete.")


if __name__ == "__main__":
    migrate()

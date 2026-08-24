import sqlite3
import json
from datetime import UTC, datetime

from app.core.config import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                result_json TEXT
            )
            """
        )
        # An append-only audit log of agent corrections/overrides, kept separate from the
        # mutable verification_jobs row it applies to - requirements.md section 6:
        # "Auditability of automated decisions, agent corrections, and overrides." Overwriting
        # verification_jobs.result_json in place preserves the current state for the UI, but
        # only this table preserves the history of what changed, when, and by whom.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id TEXT NOT NULL,
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                corrected_fields_json TEXT NOT NULL,
                note TEXT,
                overridden_by TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def save_verification(verification_id: str, status: str, result: dict) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO verification_jobs (id, status, created_at, result_json) VALUES (?, ?, ?, ?)",
            (verification_id, status, datetime.now(UTC).isoformat(), json.dumps(result)),
        )


def get_verification(verification_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT result_json FROM verification_jobs WHERE id = ?", (verification_id,)
        ).fetchone()
        return json.loads(row["result_json"]) if row and row["result_json"] else None


def record_override(
    verification_id: str,
    previous_status: str,
    new_status: str,
    corrected_fields: dict[str, str],
    note: str | None,
    overridden_by: str | None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO overrides
                (verification_id, previous_status, new_status, corrected_fields_json, note, overridden_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verification_id,
                previous_status,
                new_status,
                json.dumps(corrected_fields),
                note,
                overridden_by,
                datetime.now(UTC).isoformat(),
            ),
        )

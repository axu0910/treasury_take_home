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


def save_verification(verification_id: str, status: str, result: dict) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO verification_jobs (id, status, created_at, result_json) VALUES (?, ?, ?, ?)",
            (verification_id, status, datetime.now(UTC).isoformat(), json.dumps(result)),
        )

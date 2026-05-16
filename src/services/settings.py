"""Settings service — read/write with timestamp tracking."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def get_all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def update_settings(conn: sqlite3.Connection, updates: dict[str, str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for key, value in updates.items():
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), now),
        )
    conn.commit()

"""Cart / shortlist service — operators add candidates here before cloning."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_cart(conn: sqlite3.Connection) -> list[dict]:
    """Return all cart items, newest first."""
    rows = conn.execute(
        "SELECT * FROM cart_items ORDER BY added_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_cart_count(conn: sqlite3.Connection) -> int:
    """Return the number of items currently in the cart."""
    return conn.execute("SELECT COUNT(*) as cnt FROM cart_items").fetchone()["cnt"]


def add_to_cart(conn: sqlite3.Connection, repo: dict[str, Any]) -> tuple[int, bool]:
    """Add a repo to the cart.  Returns (item_id, already_existed).

    Idempotent by full_name — if the repo is already in the cart the existing
    item_id is returned and already_existed is True.
    """
    full_name = repo.get("full_name", "")
    if not full_name:
        raise ValueError("repo must have a full_name")

    existing = conn.execute(
        "SELECT item_id FROM cart_items WHERE full_name = ?", (full_name,)
    ).fetchone()
    if existing:
        return existing["item_id"], True

    # Normalise language: accept either a single string or a list.
    raw_lang = repo.get("language") or repo.get("languages")
    if isinstance(raw_lang, list):
        language = raw_lang[0] if raw_lang else None
    else:
        language = raw_lang or None

    cursor = conn.execute(
        """INSERT INTO cart_items
           (full_name, source_url, owner, name, language,
            stars, size_kb, license, topics, description,
            last_pushed_at, quality_score, is_fork, is_archived, added_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            full_name,
            repo.get("source_url") or f"https://github.com/{full_name}",
            repo.get("owner") or (full_name.split("/")[0] if "/" in full_name else ""),
            repo.get("name") or (full_name.split("/")[1] if "/" in full_name else ""),
            language,
            repo.get("stars", 0) or 0,
            repo.get("size_kb", 0) or 0,
            repo.get("license"),
            json.dumps(repo.get("topics", [])),
            repo.get("description") or "",
            repo.get("last_pushed_at"),
            repo.get("quality_score") or repo.get("_quality_score"),
            1 if repo.get("is_fork") else 0,
            1 if repo.get("is_archived") else 0,
            _now(),
        ),
    )
    conn.commit()
    return cursor.lastrowid, False


def remove_from_cart(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove an item by item_id.  Returns True if found and deleted."""
    row = conn.execute(
        "SELECT item_id FROM cart_items WHERE item_id = ?", (item_id,)
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM cart_items WHERE item_id = ?", (item_id,))
    conn.commit()
    return True


def clear_cart(conn: sqlite3.Connection) -> int:
    """Remove all items from the cart.  Returns count removed."""
    count = conn.execute("SELECT COUNT(*) as cnt FROM cart_items").fetchone()["cnt"]
    conn.execute("DELETE FROM cart_items")
    conn.commit()
    return count

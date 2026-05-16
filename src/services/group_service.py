"""Repo group management service."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def create_group(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    """Create a new repo group.

    Returns: group_id
    """
    now = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
        "INSERT INTO repo_groups (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, description, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def get_group(conn: sqlite3.Connection, group_id: int) -> dict | None:
    """Get a group by ID."""
    row = conn.execute(
        "SELECT group_id, name, description, created_at, updated_at FROM repo_groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()

    if not row:
        return None

    return {
        "group_id": row[0],
        "name": row[1],
        "description": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


def get_groups(conn: sqlite3.Connection) -> list[dict]:
    """Get all groups."""
    rows = conn.execute(
        "SELECT group_id, name, description, created_at, updated_at FROM repo_groups ORDER BY created_at DESC"
    ).fetchall()

    return [
        {
            "group_id": row[0],
            "name": row[1],
            "description": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }
        for row in rows
    ]


def update_group(
    conn: sqlite3.Connection, group_id: int, name: str | None = None, description: str | None = None
) -> bool:
    """Update a group. Returns True if successful."""
    group = get_group(conn, group_id)
    if not group:
        return False

    now = datetime.now(timezone.utc).isoformat()
    new_name = name if name is not None else group["name"]
    new_desc = description if description is not None else group["description"]

    conn.execute(
        "UPDATE repo_groups SET name = ?, description = ?, updated_at = ? WHERE group_id = ?",
        (new_name, new_desc, now, group_id),
    )
    conn.commit()
    return True


def delete_group(conn: sqlite3.Connection, group_id: int) -> bool:
    """Delete a group and all its items."""
    group = get_group(conn, group_id)
    if not group:
        return False

    # Delete items first
    conn.execute("DELETE FROM repo_group_items WHERE group_id = ?", (group_id,))
    # Delete group
    conn.execute("DELETE FROM repo_groups WHERE group_id = ?", (group_id,))
    conn.commit()
    return True


def add_item_to_group(
    conn: sqlite3.Connection,
    group_id: int,
    full_name: str,
    source_url: str = "",
    owner: str = "",
    name: str = "",
    niche_id: str | None = None,
) -> int | None:
    """Add a repo to a group.

    Returns: item_id, or None if group doesn't exist
    """
    # Verify group exists
    if not get_group(conn, group_id):
        return None

    # Check if already in group
    existing = conn.execute(
        "SELECT item_id FROM repo_group_items WHERE group_id = ? AND full_name = ?",
        (group_id, full_name),
    ).fetchone()

    if existing:
        return existing[0]  # Already in group

    now = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
        """INSERT INTO repo_group_items (group_id, full_name, source_url, owner, name, niche_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (group_id, full_name, source_url, owner, name, niche_id, now),
    )
    conn.commit()

    # Update group's updated_at
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE repo_groups SET updated_at = ? WHERE group_id = ?", (now, group_id))
    conn.commit()

    return cursor.lastrowid


def get_group_items(conn: sqlite3.Connection, group_id: int) -> list[dict]:
    """Get all items in a group."""
    rows = conn.execute(
        """SELECT item_id, group_id, full_name, source_url, owner, name, niche_id, created_at
           FROM repo_group_items WHERE group_id = ? ORDER BY created_at""",
        (group_id,),
    ).fetchall()

    return [
        {
            "item_id": row[0],
            "group_id": row[1],
            "full_name": row[2],
            "source_url": row[3],
            "owner": row[4],
            "name": row[5],
            "niche_id": row[6],
            "created_at": row[7],
        }
        for row in rows
    ]


def remove_item_from_group(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove an item from a group."""
    item = conn.execute(
        "SELECT group_id FROM repo_group_items WHERE item_id = ?", (item_id,)
    ).fetchone()

    if not item:
        return False

    conn.execute("DELETE FROM repo_group_items WHERE item_id = ?", (item_id,))
    conn.commit()

    # Update group's updated_at
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE repo_groups SET updated_at = ? WHERE group_id = ?", (now, item[0]))
    conn.commit()

    return True


def clone_group(
    conn: sqlite3.Connection, group_id: int, run_service=None
) -> tuple[int | None, str]:
    """Create a run from a saved group.

    Uses manual_repo_list mode internally.

    Returns: (run_id, error_message)
    """
    group = get_group(conn, group_id)
    if not group:
        return None, "Group not found"

    items = get_group_items(conn, group_id)
    if not items:
        return None, "Group is empty"

    # Build repo_inputs from group items
    repo_inputs = [item["full_name"] for item in items]

    if not run_service:
        from . import run_service as rs
        run_service = rs

    # Create run with group info in label/description
    run_id = run_service.create_run(
        conn,
        mode="manual_repo_list",
        label=f"Group: {group['name']}",
        description=group["description"],
    )

    # Submit run in background
    try:
        from ..main import thread_pool
        thread_pool.submit(
            run_service.execute_run,
            run_id,
            "manual_repo_list",
            repo_inputs,
            None,
        )
    except RuntimeError:
        # Thread pool may be shut down during testing
        # In this case, just return the run_id without submitting
        logger.debug("Thread pool not available for group clone submission")

    return run_id, ""

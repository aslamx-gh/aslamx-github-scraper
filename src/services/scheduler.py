"""APScheduler-based cron scheduling for automated runs."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, db_path=None):
        self._scheduler = BackgroundScheduler(daemon=True)
        self._db_path = db_path
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("Scheduler started")

    def stop(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._started

    def load_schedules(self, conn: sqlite3.Connection) -> None:
        """Load enabled schedules from DB and register them."""
        # Remove existing jobs
        self._scheduler.remove_all_jobs()

        rows = conn.execute(
            "SELECT * FROM schedules WHERE enabled = 1"
        ).fetchall()

        for row in rows:
            try:
                self._add_job(dict(row))
            except Exception as e:
                logger.error("Failed to load schedule %d: %s", row["schedule_id"], e)

    def _add_job(self, schedule: dict) -> None:
        cron = schedule["cron_expression"]
        trigger = CronTrigger.from_crontab(cron)
        job_id = f"schedule_{schedule['schedule_id']}"

        niche_ids = schedule.get("niche_ids", "[]")
        if isinstance(niche_ids, str):
            niche_ids = json.loads(niche_ids)

        self._scheduler.add_job(
            _trigger_scheduled_run,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs={
                "schedule_id": schedule["schedule_id"],
                "niche_ids": niche_ids,
                "db_path": self._db_path,
            },
        )
        logger.info("Registered schedule %d: %s", schedule["schedule_id"], cron)

    def get_next_run_time(self, schedule_id: int) -> str | None:
        job_id = f"schedule_{schedule_id}"
        job = self._scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None


def validate_cron(expression: str) -> tuple[bool, str | None]:
    """Validate a cron expression. Returns (valid, error_message)."""
    try:
        CronTrigger.from_crontab(expression)
        return True, None
    except (ValueError, TypeError) as e:
        return False, str(e)


def _trigger_scheduled_run(schedule_id: int, niche_ids: list[str], db_path=None) -> None:
    """Called by APScheduler when a cron job fires."""
    from .run_service import create_run, execute_run
    from ..database import get_connection

    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE schedules SET last_run_at=? WHERE schedule_id=?",
            (now, schedule_id),
        )
        conn.commit()

        run_id = create_run(conn, mode="scheduled", label=f"Scheduled run (schedule {schedule_id})")
        conn.close()

        execute_run(run_id, mode="scheduled", niche_ids=niche_ids, db_path=db_path)
    except Exception as e:
        logger.error("Scheduled run failed for schedule %d: %s", schedule_id, e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# CRUD helpers for schedules

def get_schedules(conn: sqlite3.Connection) -> list[dict]:
    from ..database import serialize_rows
    rows = conn.execute("SELECT * FROM schedules ORDER BY created_at DESC").fetchall()
    return serialize_rows(rows)


def get_schedule(conn: sqlite3.Connection, schedule_id: int) -> dict | None:
    from ..database import serialize_row
    row = conn.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
    return serialize_row(row)


def create_schedule(
    conn: sqlite3.Connection,
    name: str,
    cron_expression: str,
    niche_ids: list[str],
    enabled: bool = False,
) -> tuple[int | None, str | None]:
    valid, err = validate_cron(cron_expression)
    if not valid:
        return None, f"Invalid cron expression: {err}"

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """INSERT INTO schedules (name, cron_expression, niche_ids, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, cron_expression, json.dumps(niche_ids), 1 if enabled else 0, now, now),
    )
    conn.commit()
    return cursor.lastrowid, None


def update_schedule(
    conn: sqlite3.Connection,
    schedule_id: int,
    name: str | None = None,
    cron_expression: str | None = None,
    niche_ids: list[str] | None = None,
    enabled: bool | None = None,
) -> str | None:
    if cron_expression is not None:
        valid, err = validate_cron(cron_expression)
        if not valid:
            return f"Invalid cron expression: {err}"

    now = datetime.now(timezone.utc).isoformat()
    updates = {"updated_at": now}
    if name is not None:
        updates["name"] = name
    if cron_expression is not None:
        updates["cron_expression"] = cron_expression
    if niche_ids is not None:
        updates["niche_ids"] = json.dumps(niche_ids)
    if enabled is not None:
        updates["enabled"] = 1 if enabled else 0

    sets = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE schedules SET {sets} WHERE schedule_id=?", (*updates.values(), schedule_id))
    conn.commit()
    return None


def delete_schedule(conn: sqlite3.Connection, schedule_id: int) -> None:
    conn.execute("DELETE FROM schedules WHERE schedule_id=?", (schedule_id,))
    conn.commit()

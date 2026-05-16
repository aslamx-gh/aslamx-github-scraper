"""SQLite database initialization and connection management."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "scraper.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS niches (
    niche_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    languages TEXT NOT NULL DEFAULT '[]',
    github_search_queries TEXT NOT NULL DEFAULT '[]',
    github_topics TEXT NOT NULL DEFAULT '[]',
    exclude_terms TEXT NOT NULL DEFAULT '[]',
    min_stars INTEGER NOT NULL DEFAULT 0,
    max_repo_size_kb INTEGER NOT NULL DEFAULT 512000,
    min_recent_activity_days INTEGER NOT NULL DEFAULT 0,
    allowed_licenses TEXT NOT NULL DEFAULT '[]',
    exclude_forks INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repos (
    repo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'main',
    license TEXT,
    topics TEXT NOT NULL DEFAULT '[]',
    languages TEXT NOT NULL DEFAULT '[]',
    size_kb INTEGER NOT NULL DEFAULT 0,
    stars INTEGER NOT NULL DEFAULT 0,
    is_fork INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    last_pushed_at TEXT,
    discovered_via_niche TEXT,
    clone_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- V2 repo quality signals
    repo_quality_score REAL,
    has_docs INTEGER,
    has_tests INTEGER,
    has_examples INTEGER,
    beginner_suitability TEXT,
    generated_code_signal REAL,
    maintenance_health TEXT
);

CREATE TABLE IF NOT EXISTS repo_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(repo_id),
    commit_sha TEXT NOT NULL,
    branch TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    ingestion_status TEXT NOT NULL DEFAULT 'pending',
    extraction_status TEXT NOT NULL DEFAULT 'pending',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES repo_snapshots(snapshot_id),
    repo_id INTEGER NOT NULL REFERENCES repos(repo_id),
    relative_path TEXT NOT NULL,
    language TEXT,
    file_kind TEXT NOT NULL DEFAULT 'source',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    line_count INTEGER NOT NULL DEFAULT 0,
    included INTEGER NOT NULL DEFAULT 1,
    skip_reason TEXT,
    inspected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    snapshot_id INTEGER NOT NULL REFERENCES repo_snapshots(snapshot_id),
    repo_id INTEGER NOT NULL REFERENCES repos(repo_id),
    chunk_type TEXT NOT NULL DEFAULT 'section',
    symbol_name TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    language TEXT,
    longevity_band TEXT NOT NULL DEFAULT 'small',
    longevity_confidence REAL NOT NULL DEFAULT 0.0,
    primary_tag TEXT,
    summary TEXT,
    quality_flags TEXT NOT NULL DEFAULT '[]',
    extracted_at TEXT NOT NULL,
    -- V2 quality scores
    chunk_quality_score REAL,
    teaching_value_score REAL,
    -- V2 educational classification
    difficulty TEXT,
    beginner_safe TEXT,
    topic TEXT,
    subtopic TEXT,
    paradigm TEXT,
    style TEXT,
    architecture_level TEXT,
    security_relevance TEXT,
    example_type TEXT,
    -- V2 lifecycle state
    validation_status TEXT NOT NULL DEFAULT 'pending',
    quarantine_reason TEXT,
    publication_status TEXT NOT NULL DEFAULT 'unpublished'
);

CREATE TABLE IF NOT EXISTS chunk_tags (
    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL REFERENCES chunks(chunk_id),
    tag TEXT NOT NULL,
    tag_source TEXT NOT NULL DEFAULT 'heuristic'
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    label TEXT,
    description TEXT,
    run_inputs TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'searching',
    status_code INTEGER NOT NULL DEFAULT 99,
    total_items INTEGER NOT NULL DEFAULT 0,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    repo_full_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    status_code INTEGER NOT NULL DEFAULT 99,
    repo_id INTEGER REFERENCES repos(repo_id),
    snapshot_id INTEGER REFERENCES repo_snapshots(snapshot_id),
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS failures (
    failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(run_id),
    item_id INTEGER REFERENCES run_items(item_id),
    repo_full_name TEXT,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    is_retryable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_rejections (
    rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(run_id),
    repo_full_name TEXT NOT NULL,
    source_niche TEXT,
    source_query TEXT,
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    niche_ids TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_group_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES repo_groups(group_id),
    full_name TEXT NOT NULL,
    source_url TEXT,
    owner TEXT,
    name TEXT,
    niche_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    language TEXT,
    stars INTEGER NOT NULL DEFAULT 0,
    size_kb INTEGER NOT NULL DEFAULT 0,
    license TEXT,
    topics TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    last_pushed_at TEXT,
    quality_score REAL,
    is_fork INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "scheduler.global_enabled": "false",
    "filter.require_license": "true",
    "filter.max_repo_size_kb": "102400",
    "filter.exclude_forks": "true",
    "filter.exclude_archived": "true",
    "filter.min_recent_activity_days": "365",
    "filter.min_stars": "5",
    "discovery.max_pages": "3",
    "discovery.results_per_page": "30",
    # V2: teaching quality gate — 0.0 means disabled by default; raise to filter low-quality repos
    "discovery.quality.min_score": "0.0",
    "run.default_label": "",
    "log.retention_hours": "168",
}


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    p = db_path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def migrate_db(conn: sqlite3.Connection) -> None:
    """Safe migrations for schema evolution on existing databases."""
    migrations = [
        # Iteration 4
        "ALTER TABLE runs ADD COLUMN run_inputs TEXT NOT NULL DEFAULT '{}'",
        # Iteration 7
        "ALTER TABLE repo_snapshots ADD COLUMN extraction_status TEXT NOT NULL DEFAULT 'pending'",
        """CREATE TABLE IF NOT EXISTS files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES repo_snapshots(snapshot_id),
            repo_id INTEGER NOT NULL REFERENCES repos(repo_id),
            relative_path TEXT NOT NULL,
            language TEXT,
            file_kind TEXT NOT NULL DEFAULT 'source',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            line_count INTEGER NOT NULL DEFAULT 0,
            included INTEGER NOT NULL DEFAULT 1,
            skip_reason TEXT,
            inspected_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS chunks (
            chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(file_id),
            snapshot_id INTEGER NOT NULL REFERENCES repo_snapshots(snapshot_id),
            repo_id INTEGER NOT NULL REFERENCES repos(repo_id),
            chunk_type TEXT NOT NULL DEFAULT 'section',
            symbol_name TEXT,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            language TEXT,
            longevity_band TEXT NOT NULL DEFAULT 'small',
            longevity_confidence REAL NOT NULL DEFAULT 0.0,
            primary_tag TEXT,
            summary TEXT,
            quality_flags TEXT NOT NULL DEFAULT '[]',
            extracted_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS chunk_tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER NOT NULL REFERENCES chunks(chunk_id),
            tag TEXT NOT NULL,
            tag_source TEXT NOT NULL DEFAULT 'heuristic'
        )""",
        # V2 Phase 1 — repo quality signals
        "ALTER TABLE repos ADD COLUMN repo_quality_score REAL",
        "ALTER TABLE repos ADD COLUMN has_docs INTEGER",
        "ALTER TABLE repos ADD COLUMN has_tests INTEGER",
        "ALTER TABLE repos ADD COLUMN has_examples INTEGER",
        "ALTER TABLE repos ADD COLUMN beginner_suitability TEXT",
        "ALTER TABLE repos ADD COLUMN generated_code_signal REAL",
        "ALTER TABLE repos ADD COLUMN maintenance_health TEXT",
        # V2 Phase 1 — chunk quality scores
        "ALTER TABLE chunks ADD COLUMN chunk_quality_score REAL",
        "ALTER TABLE chunks ADD COLUMN teaching_value_score REAL",
        # V2 Phase 1 — chunk educational classification
        "ALTER TABLE chunks ADD COLUMN difficulty TEXT",
        "ALTER TABLE chunks ADD COLUMN beginner_safe TEXT",
        "ALTER TABLE chunks ADD COLUMN topic TEXT",
        "ALTER TABLE chunks ADD COLUMN subtopic TEXT",
        "ALTER TABLE chunks ADD COLUMN paradigm TEXT",
        "ALTER TABLE chunks ADD COLUMN style TEXT",
        "ALTER TABLE chunks ADD COLUMN architecture_level TEXT",
        "ALTER TABLE chunks ADD COLUMN security_relevance TEXT",
        "ALTER TABLE chunks ADD COLUMN example_type TEXT",
        # V2 Phase 1 — chunk lifecycle state
        "ALTER TABLE chunks ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE chunks ADD COLUMN quarantine_reason TEXT",
        "ALTER TABLE chunks ADD COLUMN publication_status TEXT NOT NULL DEFAULT 'unpublished'",
        # Quality-first rewrite — cart/shortlist
        """CREATE TABLE IF NOT EXISTS cart_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL UNIQUE,
            source_url TEXT NOT NULL DEFAULT '',
            owner TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            language TEXT,
            stars INTEGER NOT NULL DEFAULT 0,
            size_kb INTEGER NOT NULL DEFAULT 0,
            license TEXT,
            topics TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',
            last_pushed_at TEXT,
            quality_score REAL,
            is_fork INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL
        )""",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Column/table already exists — skip


def seed_defaults(conn: sqlite3.Connection) -> None:
    now = _now()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
    conn.commit()


def upsert_niches(conn: sqlite3.Connection, niches: list[dict]) -> None:
    now = _now()
    for n in niches:
        existing = conn.execute(
            "SELECT niche_id FROM niches WHERE niche_id = ?", (n["niche_id"],)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE niches SET title=?, description=?, languages=?, github_search_queries=?,
                   github_topics=?, exclude_terms=?, min_stars=?, max_repo_size_kb=?,
                   min_recent_activity_days=?, allowed_licenses=?, exclude_forks=?, updated_at=?
                   WHERE niche_id=?""",
                (
                    n["title"],
                    n.get("description", ""),
                    json.dumps(n.get("languages", [])),
                    json.dumps(n.get("github_search_queries", [])),
                    json.dumps(n.get("github_topics", [])),
                    json.dumps(n.get("exclude_terms", [])),
                    n.get("min_stars", 0),
                    n.get("max_repo_size_kb", 512000),
                    n.get("min_recent_activity_days", 0),
                    json.dumps(n.get("allowed_licenses", [])),
                    1 if n.get("exclude_forks", True) else 0,
                    now,
                    n["niche_id"],
                ),
            )
        else:
            conn.execute(
                """INSERT INTO niches (niche_id, title, description, languages, github_search_queries,
                   github_topics, exclude_terms, min_stars, max_repo_size_kb,
                   min_recent_activity_days, allowed_licenses, exclude_forks, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    n["niche_id"],
                    n["title"],
                    n.get("description", ""),
                    json.dumps(n.get("languages", [])),
                    json.dumps(n.get("github_search_queries", [])),
                    json.dumps(n.get("github_topics", [])),
                    json.dumps(n.get("exclude_terms", [])),
                    n.get("min_stars", 0),
                    n.get("max_repo_size_kb", 512000),
                    n.get("min_recent_activity_days", 0),
                    json.dumps(n.get("allowed_licenses", [])),
                    1 if n.get("exclude_forks", True) else 0,
                    now,
                    now,
                ),
            )
    conn.commit()


def serialize_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for key, val in d.items():
        if isinstance(val, str) and val.startswith("["):
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
        elif isinstance(val, int) and key.startswith(("is_", "enabled", "exclude_")):
            d[key] = bool(val)
    return d


def serialize_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [serialize_row(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

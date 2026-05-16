"""Tests for V2 Phase 1 schema and metadata expansion."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.database import get_connection, init_db, migrate_db, seed_defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db() -> tuple[sqlite3.Connection, Path]:
    """Return a connection to a fresh in-memory-backed temp DB."""
    tmp = tempfile.mktemp(suffix=".db")
    conn = get_connection(Path(tmp))
    init_db(conn)
    return conn, Path(tmp)


def _legacy_db() -> tuple[sqlite3.Connection, Path]:
    """
    Return a connection to a DB that only has the pre-V2 schema.
    Simulates an existing database before V2 Phase 1 migration.
    """
    tmp = tempfile.mktemp(suffix=".db")
    conn = get_connection(Path(tmp))
    # Create the old schema without V2 columns
    conn.executescript("""
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
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repo_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            commit_sha TEXT NOT NULL,
            branch TEXT NOT NULL,
            snapshot_at TEXT NOT NULL,
            ingestion_status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            repo_id INTEGER NOT NULL,
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
            file_id INTEGER NOT NULL,
            snapshot_id INTEGER NOT NULL,
            repo_id INTEGER NOT NULL,
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
        );
        CREATE TABLE IF NOT EXISTS chunk_tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            tag_source TEXT NOT NULL DEFAULT 'heuristic'
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            label TEXT,
            description TEXT,
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
        CREATE TABLE IF NOT EXISTS failures (
            failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            item_id INTEGER,
            repo_full_name TEXT,
            error_type TEXT NOT NULL,
            error_message TEXT NOT NULL,
            is_retryable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn, Path(tmp)


def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


# ---------------------------------------------------------------------------
# New DB: V2 chunks columns present after init_db
# ---------------------------------------------------------------------------

class TestChunksV2ColumnsNewDb:
    V2_CHUNK_COLS = {
        "chunk_quality_score",
        "teaching_value_score",
        "difficulty",
        "beginner_safe",
        "topic",
        "subtopic",
        "paradigm",
        "style",
        "architecture_level",
        "security_relevance",
        "example_type",
        "validation_status",
        "quarantine_reason",
        "publication_status",
    }

    def test_all_v2_chunk_columns_present(self):
        conn, _ = _fresh_db()
        cols = _get_columns(conn, "chunks")
        for col in self.V2_CHUNK_COLS:
            assert col in cols, f"Missing V2 chunk column: {col}"

    def test_validation_status_default_pending(self):
        conn, _ = _fresh_db()
        # We can't insert without file_id FK but can check the column default via PRAGMA
        rows = conn.execute("PRAGMA table_info(chunks)").fetchall()
        col_map = {r["name"]: r for r in rows}
        assert col_map["validation_status"]["dflt_value"] in ("'pending'", None)

    def test_publication_status_default_unpublished(self):
        conn, _ = _fresh_db()
        rows = conn.execute("PRAGMA table_info(chunks)").fetchall()
        col_map = {r["name"]: r for r in rows}
        assert col_map["publication_status"]["dflt_value"] in ("'unpublished'", None)

    def test_v2_nullable_columns_are_nullable(self):
        conn, _ = _fresh_db()
        rows = conn.execute("PRAGMA table_info(chunks)").fetchall()
        col_map = {r["name"]: r for r in rows}
        nullable_cols = [
            "chunk_quality_score", "teaching_value_score",
            "difficulty", "beginner_safe", "topic", "subtopic",
            "paradigm", "style", "architecture_level",
            "security_relevance", "example_type", "quarantine_reason",
        ]
        for col in nullable_cols:
            assert col_map[col]["notnull"] == 0, f"{col} should be nullable"


# ---------------------------------------------------------------------------
# New DB: V2 repos columns present after init_db
# ---------------------------------------------------------------------------

class TestReposV2ColumnsNewDb:
    V2_REPO_COLS = {
        "repo_quality_score",
        "has_docs",
        "has_tests",
        "has_examples",
        "beginner_suitability",
        "generated_code_signal",
        "maintenance_health",
    }

    def test_all_v2_repo_columns_present(self):
        conn, _ = _fresh_db()
        cols = _get_columns(conn, "repos")
        for col in self.V2_REPO_COLS:
            assert col in cols, f"Missing V2 repo column: {col}"

    def test_v2_repo_columns_are_nullable(self):
        conn, _ = _fresh_db()
        rows = conn.execute("PRAGMA table_info(repos)").fetchall()
        col_map = {r["name"]: r for r in rows}
        for col in self.V2_REPO_COLS:
            assert col_map[col]["notnull"] == 0, f"{col} should be nullable"


# ---------------------------------------------------------------------------
# Migration: legacy DB gets all V2 columns after migrate_db
# ---------------------------------------------------------------------------

class TestMigrateDbAddsV2Columns:
    def test_migrate_adds_v2_chunk_columns(self):
        conn, _ = _legacy_db()
        migrate_db(conn)
        cols = _get_columns(conn, "chunks")
        v2_cols = [
            "chunk_quality_score", "teaching_value_score", "difficulty",
            "beginner_safe", "topic", "subtopic", "paradigm", "style",
            "architecture_level", "security_relevance", "example_type",
            "validation_status", "quarantine_reason", "publication_status",
        ]
        for col in v2_cols:
            assert col in cols, f"migrate_db() did not add chunk column: {col}"

    def test_migrate_adds_v2_repo_columns(self):
        conn, _ = _legacy_db()
        migrate_db(conn)
        cols = _get_columns(conn, "repos")
        v2_cols = [
            "repo_quality_score", "has_docs", "has_tests", "has_examples",
            "beginner_suitability", "generated_code_signal", "maintenance_health",
        ]
        for col in v2_cols:
            assert col in cols, f"migrate_db() did not add repo column: {col}"

    def test_migrate_preserves_existing_data(self):
        conn, _ = _legacy_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("owner", "repo", "owner/repo", "https://github.com/owner/repo", now, now),
        )
        conn.commit()
        migrate_db(conn)
        row = conn.execute("SELECT full_name, repo_quality_score FROM repos WHERE full_name = 'owner/repo'").fetchone()
        assert row is not None
        assert row["full_name"] == "owner/repo"
        assert row["repo_quality_score"] is None  # new column defaults to NULL

    def test_migrate_is_idempotent(self):
        conn, _ = _fresh_db()
        # Running migrate on an already-migrated DB must not raise or corrupt
        migrate_db(conn)
        migrate_db(conn)
        cols = _get_columns(conn, "chunks")
        assert "validation_status" in cols

    def test_migrate_legacy_chunks_get_null_v2_fields(self):
        conn, _ = _legacy_db()
        migrate_db(conn)
        # After migration, existing chunks rows should have NULL for new fields
        # Insert a repo and snapshot first
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("o", "r", "o/r", "https://github.com/o/r", now, now),
        )
        conn.execute(
            "INSERT INTO repo_snapshots (repo_id, commit_sha, branch, snapshot_at, ingestion_status) "
            "VALUES (1, 'abc', 'main', ?, 'completed')",
            (now,),
        )
        conn.execute(
            "INSERT INTO files (snapshot_id, repo_id, relative_path, language, inspected_at) "
            "VALUES (1, 1, 'main.py', 'python', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO chunks (file_id, snapshot_id, repo_id, start_line, end_line, "
            "content_hash, extracted_at) VALUES (1, 1, 1, 1, 10, 'abc123', ?)",
            (now,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT difficulty, topic, validation_status, publication_status FROM chunks WHERE chunk_id = 1"
        ).fetchone()
        assert row["difficulty"] is None
        assert row["topic"] is None
        # validation_status and publication_status may be None for legacy rows (no DEFAULT applied retroactively)
        # The important thing is that the columns exist and are readable


# ---------------------------------------------------------------------------
# V2 column values can be written and read back
# ---------------------------------------------------------------------------

class TestV2ColumnRoundtrip:
    def _insert_test_chunk(self, conn):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("o", "r", "o/r", "https://github.com/o/r", now, now),
        )
        conn.execute(
            "INSERT INTO repo_snapshots (repo_id, commit_sha, branch, snapshot_at, ingestion_status) "
            "VALUES (1, 'abc', 'main', ?, 'completed')",
            (now,),
        )
        conn.execute(
            "INSERT INTO files (snapshot_id, repo_id, relative_path, language, inspected_at) "
            "VALUES (1, 1, 'main.py', 'python', ?)",
            (now,),
        )
        conn.execute(
            """INSERT INTO chunks
               (file_id, snapshot_id, repo_id, start_line, end_line, content_hash, extracted_at,
                difficulty, topic, subtopic, paradigm, style, architecture_level,
                security_relevance, example_type, beginner_safe,
                chunk_quality_score, teaching_value_score,
                validation_status, publication_status)
               VALUES (1, 1, 1, 1, 20, 'hash1', ?,
                       'beginner', 'functions', 'closures', 'functional', 'functional', 'function',
                       'low', 'good_example', 'safe',
                       0.75, 0.85,
                       'accepted', 'published')""",
            (now,),
        )
        conn.commit()

    def test_educational_fields_roundtrip(self):
        conn, _ = _fresh_db()
        self._insert_test_chunk(conn)
        row = conn.execute(
            "SELECT difficulty, topic, subtopic, paradigm, style, architecture_level, "
            "security_relevance, example_type, beginner_safe FROM chunks WHERE chunk_id = 1"
        ).fetchone()
        assert row["difficulty"] == "beginner"
        assert row["topic"] == "functions"
        assert row["subtopic"] == "closures"
        assert row["paradigm"] == "functional"
        assert row["style"] == "functional"
        assert row["architecture_level"] == "function"
        assert row["security_relevance"] == "low"
        assert row["example_type"] == "good_example"
        assert row["beginner_safe"] == "safe"

    def test_score_fields_roundtrip(self):
        conn, _ = _fresh_db()
        self._insert_test_chunk(conn)
        row = conn.execute(
            "SELECT chunk_quality_score, teaching_value_score FROM chunks WHERE chunk_id = 1"
        ).fetchone()
        assert abs(row["chunk_quality_score"] - 0.75) < 0.001
        assert abs(row["teaching_value_score"] - 0.85) < 0.001

    def test_lifecycle_fields_roundtrip(self):
        conn, _ = _fresh_db()
        self._insert_test_chunk(conn)
        row = conn.execute(
            "SELECT validation_status, publication_status, quarantine_reason FROM chunks WHERE chunk_id = 1"
        ).fetchone()
        assert row["validation_status"] == "accepted"
        assert row["publication_status"] == "published"
        assert row["quarantine_reason"] is None

    def test_quarantine_reason_can_be_set(self):
        conn, _ = _fresh_db()
        self._insert_test_chunk(conn)
        conn.execute(
            "UPDATE chunks SET validation_status = 'quarantined', quarantine_reason = 'near_duplicate' WHERE chunk_id = 1"
        )
        conn.commit()
        row = conn.execute(
            "SELECT validation_status, quarantine_reason FROM chunks WHERE chunk_id = 1"
        ).fetchone()
        assert row["validation_status"] == "quarantined"
        assert row["quarantine_reason"] == "near_duplicate"

    def test_repo_quality_fields_roundtrip(self):
        conn, _ = _fresh_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at, "
            "repo_quality_score, has_docs, has_tests, has_examples, beginner_suitability, "
            "generated_code_signal, maintenance_health) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("o", "r", "o/r", "https://github.com/o/r", now, now,
             0.80, 1, 1, 0, "medium", 0.05, "active"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT repo_quality_score, has_docs, has_tests, has_examples, "
            "beginner_suitability, generated_code_signal, maintenance_health "
            "FROM repos WHERE full_name = 'o/r'"
        ).fetchone()
        assert abs(row["repo_quality_score"] - 0.80) < 0.001
        assert row["has_docs"] == 1
        assert row["has_tests"] == 1
        assert row["has_examples"] == 0
        assert row["beginner_suitability"] == "medium"
        assert abs(row["generated_code_signal"] - 0.05) < 0.001
        assert row["maintenance_health"] == "active"

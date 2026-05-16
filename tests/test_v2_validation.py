"""Tests for V2 Phase 5: validation, deduplication, and quarantine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat()


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.database import init_db, migrate_db, seed_defaults
    init_db(conn)
    migrate_db(conn)
    seed_defaults(conn)
    conn.close()
    return db_path


def _seed_repo(conn: sqlite3.Connection, full_name: str = "owner/repo") -> int:
    owner, name = full_name.split("/", 1)
    cursor = conn.execute(
        """INSERT INTO repos (owner, name, full_name, source_url, default_branch,
           license, topics, languages, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (owner, name, full_name, f"https://github.com/{full_name}",
         "main", "MIT", "[]", "[]", _now(), _now()),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_snapshot(conn: sqlite3.Connection, repo_id: int) -> int:
    cursor = conn.execute(
        """INSERT INTO repo_snapshots (repo_id, commit_sha, branch, snapshot_at,
           ingestion_status, extraction_status)
           VALUES (?,?,?,?,?,?)""",
        (repo_id, "abc123", "main", _now(), "completed", "completed"),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_chunk(
    conn: sqlite3.Connection,
    snapshot_id: int,
    repo_id: int,
    *,
    content_hash: str = "hash_aaa",
    quality_flags: list | None = None,
    validation_status: str = "pending",
    longevity_band: str = "medium",
    primary_tag: str = "utilities",
    file_id: int = 1,
) -> int:
    flags_json = json.dumps(quality_flags or [])
    cursor = conn.execute(
        """INSERT INTO chunks
           (file_id, snapshot_id, repo_id, chunk_type, symbol_name,
            start_line, end_line, content_hash, language,
            longevity_band, longevity_confidence, primary_tag, summary,
            quality_flags, extracted_at, validation_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            file_id, snapshot_id, repo_id, "function", "my_func",
            1, 10, content_hash, "python",
            longevity_band, 0.8, primary_tag, "Function my_func",
            flags_json, _now(), validation_status,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# validate_pending_chunks — core behaviour
# ---------------------------------------------------------------------------

class TestValidatePendingChunks:
    def test_accepts_clean_chunk(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(conn, snap_id, repo_id, content_hash="unique_hash_001")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path, snapshot_id=snap_id)

        assert report.accepted == 1
        assert report.quarantined == 0
        assert report.pending_count == 1

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT validation_status FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()["validation_status"]
        conn.close()
        assert status == "accepted"

    def test_returns_validation_report(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        _seed_chunk(conn, snap_id, repo_id, content_hash="unique_hash_rpt")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)

        assert hasattr(report, "pending_count")
        assert hasattr(report, "accepted")
        assert hasattr(report, "quarantined")
        assert hasattr(report, "by_reason")
        assert hasattr(report, "errors")

    def test_nothing_to_do_on_empty_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)
        assert report.pending_count == 0
        assert report.accepted == 0
        assert report.quarantined == 0

    def test_skips_already_validated_chunks(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        # Seed one already-accepted chunk
        _seed_chunk(conn, snap_id, repo_id, content_hash="h001", validation_status="accepted")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)
        assert report.pending_count == 0

    def test_scoped_to_snapshot(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_a = _seed_snapshot(conn, repo_id)
        snap_b = _seed_snapshot(conn, repo_id)
        _seed_chunk(conn, snap_a, repo_id, content_hash="h_snap_a")
        _seed_chunk(conn, snap_b, repo_id, content_hash="h_snap_b")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path, snapshot_id=snap_a)

        assert report.pending_count == 1
        assert report.accepted + report.quarantined == 1

        # snap_b chunk should still be pending
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT validation_status FROM chunks WHERE snapshot_id=?", (snap_b,)
        ).fetchone()["validation_status"]
        conn.close()
        assert status == "pending"


# ---------------------------------------------------------------------------
# Exact duplicate detection
# ---------------------------------------------------------------------------

class TestExactDuplicateDetection:
    def test_quarantines_duplicate_hash(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)

        # First chunk already accepted
        _seed_chunk(conn, snap_id, repo_id,
                    content_hash="dup_hash", validation_status="accepted")
        # Second chunk pending with same hash
        dup_id = _seed_chunk(conn, snap_id, repo_id,
                             content_hash="dup_hash", validation_status="pending")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)

        assert report.quarantined >= 1
        assert report.by_reason.get("exact_duplicate", 0) >= 1

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT validation_status, quarantine_reason FROM chunks WHERE chunk_id=?",
            (dup_id,)
        ).fetchone()
        conn.close()
        assert row["validation_status"] == "quarantined"
        assert row["quarantine_reason"] == "exact_duplicate"

    def test_cross_repo_duplicate_detection(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_a = _seed_repo(conn, "owner/repo_a")
        repo_b = _seed_repo(conn, "owner/repo_b")
        snap_a = _seed_snapshot(conn, repo_a)
        snap_b = _seed_snapshot(conn, repo_b)

        # repo_a has an accepted chunk
        _seed_chunk(conn, snap_a, repo_a,
                    content_hash="cross_repo_hash", validation_status="accepted")
        # repo_b has a pending chunk with the same hash
        dup_id = _seed_chunk(conn, snap_b, repo_b,
                             content_hash="cross_repo_hash", validation_status="pending")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)

        assert report.by_reason.get("exact_duplicate", 0) >= 1

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT validation_status FROM chunks WHERE chunk_id=?", (dup_id,)
        ).fetchone()["validation_status"]
        conn.close()
        assert status == "quarantined"

    def test_unique_hash_not_quarantined(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(conn, snap_id, repo_id, content_hash="unique_xyz_999")
        conn.close()

        from src.services.validation import validate_pending_chunks
        validate_pending_chunks(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT validation_status FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()["validation_status"]
        conn.close()
        assert status == "accepted"


# ---------------------------------------------------------------------------
# Boilerplate detection (quality_flags)
# ---------------------------------------------------------------------------

class TestBoilerplateDetection:
    def test_quarantines_very_short_mostly_comments(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(
            conn, snap_id, repo_id,
            content_hash="bp_hash_001",
            quality_flags=["very_short", "mostly_comments"],
        )
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)

        assert report.quarantined == 1
        assert report.by_reason.get("boilerplate", 0) == 1

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT validation_status, quarantine_reason FROM chunks WHERE chunk_id=?",
            (chunk_id,)
        ).fetchone()
        conn.close()
        assert row["validation_status"] == "quarantined"
        assert row["quarantine_reason"] == "boilerplate"

    def test_very_short_alone_not_quarantined(self, tmp_path):
        """very_short without mostly_comments should be accepted."""
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(
            conn, snap_id, repo_id,
            content_hash="short_only_hash",
            quality_flags=["very_short"],
        )
        conn.close()

        from src.services.validation import validate_pending_chunks
        validate_pending_chunks(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT validation_status FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()["validation_status"]
        conn.close()
        assert status == "accepted"

    def test_quarantines_placeholder(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(
            conn, snap_id, repo_id,
            content_hash="ph_hash_001",
            quality_flags=["has_placeholder"],
        )
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)

        assert report.quarantined == 1
        assert report.by_reason.get("placeholder", 0) == 1

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT validation_status, quarantine_reason FROM chunks WHERE chunk_id=?",
            (chunk_id,)
        ).fetchone()
        conn.close()
        assert row["validation_status"] == "quarantined"
        assert row["quarantine_reason"] == "placeholder"

    def test_no_flags_not_quarantined(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(
            conn, snap_id, repo_id,
            content_hash="no_flags_hash",
            quality_flags=[],
        )
        conn.close()

        from src.services.validation import validate_pending_chunks
        validate_pending_chunks(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute(
            "SELECT validation_status FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()["validation_status"]
        conn.close()
        assert status == "accepted"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_does_not_change_already_accepted(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        _seed_chunk(conn, snap_id, repo_id, content_hash="idem_hash_001")
        conn.close()

        from src.services.validation import validate_pending_chunks
        r1 = validate_pending_chunks(db_path=db_path)
        r2 = validate_pending_chunks(db_path=db_path)

        assert r1.accepted == 1
        assert r2.pending_count == 0  # nothing left to process

    def test_rerun_does_not_change_quarantined(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        _seed_chunk(
            conn, snap_id, repo_id,
            content_hash="idem_bp_hash",
            quality_flags=["very_short", "mostly_comments"],
        )
        conn.close()

        from src.services.validation import validate_pending_chunks
        r1 = validate_pending_chunks(db_path=db_path)
        r2 = validate_pending_chunks(db_path=db_path)

        assert r1.quarantined == 1
        assert r2.pending_count == 0

    def test_multiple_chunks_all_processed(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        for i in range(5):
            _seed_chunk(conn, snap_id, repo_id, content_hash=f"multi_hash_{i:03d}")
        conn.close()

        from src.services.validation import validate_pending_chunks
        report = validate_pending_chunks(db_path=db_path)

        assert report.pending_count == 5
        assert report.accepted + report.quarantined == 5


# ---------------------------------------------------------------------------
# Quarantine record writing
# ---------------------------------------------------------------------------

class TestQuarantineRecord:
    def test_quarantine_record_written_to_disk(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        chunk_id = _seed_chunk(
            conn, snap_id, repo_id,
            content_hash="qr_write_hash",
            quality_flags=["very_short", "mostly_comments"],
        )
        conn.close()

        from unittest.mock import patch
        from src.services import storage as st
        quarantine_dir = tmp_path / "quarantine"

        with patch.object(st, "QUARANTINE_DIR", quarantine_dir):
            from src.services.validation import validate_pending_chunks
            validate_pending_chunks(db_path=db_path)

        records = list(quarantine_dir.glob("chunk_*.json"))
        assert len(records) >= 1
        data = json.loads(records[0].read_text())
        assert data["chunk_id"] == chunk_id
        assert data["quarantine_reason"] == "boilerplate"
        assert "content_hash" in data

    def test_no_quarantine_record_for_accepted(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        _seed_chunk(conn, snap_id, repo_id, content_hash="acc_noquarantine")
        conn.close()

        from unittest.mock import patch
        from src.services import storage as st
        quarantine_dir = tmp_path / "quarantine"

        with patch.object(st, "QUARANTINE_DIR", quarantine_dir):
            from src.services.validation import validate_pending_chunks
            validate_pending_chunks(db_path=db_path)

        records = list(quarantine_dir.glob("chunk_*.json")) if quarantine_dir.exists() else []
        assert len(records) == 0


# ---------------------------------------------------------------------------
# get_validation_summary
# ---------------------------------------------------------------------------

class TestGetValidationSummary:
    def test_summary_counts(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        _seed_chunk(conn, snap_id, repo_id, content_hash="s_acc", validation_status="accepted")
        _seed_chunk(conn, snap_id, repo_id, content_hash="s_pend", validation_status="pending")
        _seed_chunk(conn, snap_id, repo_id, content_hash="s_quar", validation_status="quarantined")
        conn.close()

        from src.services.validation import get_validation_summary
        summary = get_validation_summary(db_path=db_path)

        assert summary["total_chunks"] == 3
        assert summary["by_status"]["accepted"] == 1
        assert summary["by_status"]["pending"] == 1
        assert summary["by_status"]["quarantined"] == 1

    def test_summary_empty_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        from src.services.validation import get_validation_summary
        summary = get_validation_summary(db_path=db_path)
        assert summary["total_chunks"] == 0
        assert summary["by_status"] == {}

    def test_summary_fields_present(self, tmp_path):
        db_path = _make_db(tmp_path)
        from src.services.validation import get_validation_summary
        summary = get_validation_summary(db_path=db_path)
        assert "total_chunks" in summary
        assert "pending_chunks" in summary
        assert "by_status" in summary
        assert "quarantine_by_reason" in summary


# ---------------------------------------------------------------------------
# API endpoint smoke tests
# ---------------------------------------------------------------------------

class TestValidationAPIEndpoints:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                yield c, db_path

    def test_validation_summary_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                resp = c.get("/api/validation/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_chunks" in data
        assert "by_status" in data
        assert "quarantine_by_reason" in data

    def test_validation_run_nothing_to_do(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                resp = c.post("/api/validation/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "nothing_to_do"

    def test_validation_run_queued(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        _seed_chunk(conn, snap_id, repo_id, content_hash="api_test_hash")
        conn.close()

        with patch("src.database.DB_PATH", db_path), \
             patch("src.main.thread_pool") as mock_pool:
            mock_pool.submit.return_value = None
            from src.main import app
            with TestClient(app) as c:
                resp = c.post("/api/validation/run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["pending_chunks"] == 1

    def test_validation_ui_page(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                resp = c.get("/validation")
        assert resp.status_code == 200
        assert b"Validation" in resp.content

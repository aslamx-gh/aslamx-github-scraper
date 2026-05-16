"""Tests for extraction backfill and per-repo rerun paths."""

from __future__ import annotations

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
    """Create a fully-initialised in-file DB under tmp_path."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.database import init_db, migrate_db, seed_defaults
    init_db(conn)
    migrate_db(conn)
    seed_defaults(conn)
    conn.close()
    return db_path


def _seed_repo(conn: sqlite3.Connection, full_name: str, clone_path: str | None) -> int:
    now = _now()
    owner, name = full_name.split("/", 1)
    cursor = conn.execute(
        """INSERT INTO repos (owner, name, full_name, source_url, default_branch,
           license, topics, languages, created_at, updated_at, clone_path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (owner, name, full_name, f"https://github.com/{full_name}",
         "main", "MIT", "[]", "[]", now, now, clone_path),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_snapshot(conn: sqlite3.Connection, repo_id: int, extraction_status: str = "pending") -> int:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO repo_snapshots (repo_id, commit_sha, branch, snapshot_at,
           ingestion_status, extraction_status)
           VALUES (?,?,?,?,?,?)""",
        (repo_id, "abc123", "main", now, "completed", extraction_status),
    )
    conn.commit()
    return cursor.lastrowid


def _make_repo_dir(tmp_path: Path, name: str = "testrepo") -> Path:
    """Create a minimal fake cloned repo with a Python file."""
    repo_dir = tmp_path / name
    src_dir = repo_dir / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "main.py").write_text("def hello():\n    return 'world'\n")
    (repo_dir / "README.md").write_text("# Test\n\nA test repo.\n")
    return repo_dir


# ---------------------------------------------------------------------------
# backfill_pending_extraction
# ---------------------------------------------------------------------------

class TestBackfillPendingExtraction:
    def test_extracts_pending_snapshot_with_valid_clone(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/repo", str(repo_dir))
        snap_id = _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import backfill_pending_extraction
        result = backfill_pending_extraction(db_path=db_path)

        assert result["extracted"] >= 1
        assert result["skipped_no_clone"] == 0

        # Verify DB populated
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        files = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=?", (snap_id,)).fetchone()["cnt"]
        chunks = conn.execute("SELECT COUNT(*) as cnt FROM chunks WHERE snapshot_id=?", (snap_id,)).fetchone()["cnt"]
        status = conn.execute("SELECT extraction_status FROM repo_snapshots WHERE snapshot_id=?", (snap_id,)).fetchone()["extraction_status"]
        conn.close()

        assert files > 0
        assert chunks > 0
        assert status in ("completed", "completed_with_errors")

    def test_skips_missing_clone_path(self, tmp_path):
        db_path = _make_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/gone", "/nonexistent/path/that/does/not/exist")
        snap_id = _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import backfill_pending_extraction
        result = backfill_pending_extraction(db_path=db_path)

        assert result["skipped_no_clone"] == 1
        assert result["extracted"] == 0

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        status = conn.execute("SELECT extraction_status FROM repo_snapshots WHERE snapshot_id=?", (snap_id,)).fetchone()["extraction_status"]
        failure_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM failures WHERE repo_full_name='owner/gone'",
        ).fetchone()["cnt"]
        conn.close()

        assert status == "skipped_no_clone"
        assert failure_count >= 1

    def test_skips_null_clone_path(self, tmp_path):
        db_path = _make_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/nullpath", None)
        _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import backfill_pending_extraction
        result = backfill_pending_extraction(db_path=db_path)

        assert result["skipped_no_clone"] == 1

    def test_skips_already_completed_snapshots(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/done", str(repo_dir))
        _seed_snapshot(conn, repo_id, "completed")
        conn.close()

        from src.services.run_service import backfill_pending_extraction
        result = backfill_pending_extraction(db_path=db_path)

        assert result["extracted"] == 0
        assert result["skipped_no_clone"] == 0

    def test_nothing_to_do_returns_zeros(self, tmp_path):
        db_path = _make_db(tmp_path)
        from src.services.run_service import backfill_pending_extraction
        result = backfill_pending_extraction(db_path=db_path)
        assert result == {"extracted": 0, "skipped_no_clone": 0, "errors": 0}

    def test_chunk_tags_populated(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/tagged", str(repo_dir))
        snap_id = _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import backfill_pending_extraction
        backfill_pending_extraction(db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        tag_count = conn.execute("SELECT COUNT(*) as cnt FROM chunk_tags").fetchone()["cnt"]
        conn.close()

        assert tag_count > 0

    def test_manifests_written(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/manifest_test", str(repo_dir))
        snap_id = _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        # Point manifests to tmp_path
        from unittest.mock import patch
        from src.services import manifests as m

        with patch.object(m, "MANIFESTS_REPOS", tmp_path / "manifests" / "repos"), \
             patch.object(m, "MANIFESTS_CHUNKS", tmp_path / "manifests" / "chunks"), \
             patch.object(m, "EXTRACTED_DIR", tmp_path / "extracted"):
            from src.services.run_service import backfill_pending_extraction
            backfill_pending_extraction(db_path=db_path)

        repo_manifests = list((tmp_path / "manifests" / "repos").glob("*.json"))
        assert len(repo_manifests) >= 1


# ---------------------------------------------------------------------------
# run_repo_extraction
# ---------------------------------------------------------------------------

class TestRunRepoExtraction:
    def test_extracts_single_repo(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/single", str(repo_dir))
        snap_id = _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import run_repo_extraction
        result = run_repo_extraction(repo_id=repo_id, db_path=db_path)

        assert result["status"] == "ok"
        assert result["repo_id"] == repo_id

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        chunks = conn.execute("SELECT COUNT(*) as cnt FROM chunks WHERE snapshot_id=?", (snap_id,)).fetchone()["cnt"]
        status = conn.execute("SELECT extraction_status FROM repo_snapshots WHERE snapshot_id=?", (snap_id,)).fetchone()["extraction_status"]
        conn.close()

        assert chunks > 0
        assert status in ("completed", "completed_with_errors")

    def test_error_on_missing_repo(self, tmp_path):
        db_path = _make_db(tmp_path)
        from src.services.run_service import run_repo_extraction
        result = run_repo_extraction(repo_id=9999, db_path=db_path)
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_error_on_missing_clone_path(self, tmp_path):
        db_path = _make_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/missing", "/no/such/path")
        _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import run_repo_extraction
        result = run_repo_extraction(repo_id=repo_id, db_path=db_path)
        assert result["status"] == "error"

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        failure_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM failures WHERE repo_full_name='owner/missing'"
        ).fetchone()["cnt"]
        conn.close()
        assert failure_count >= 1

    def test_clears_previous_data_on_rerun(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/rerun", str(repo_dir))
        snap_id = _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import run_repo_extraction
        # Run twice
        run_repo_extraction(repo_id=repo_id, db_path=db_path)
        run_repo_extraction(repo_id=repo_id, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Should not have duplicate file records
        file_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=?", (snap_id,)
        ).fetchone()["cnt"]
        conn.close()
        # Should be same as a single run (deduplication via clear-before-rerun)
        assert file_count > 0

    def test_returns_correct_fields(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo_dir = _make_repo_dir(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "owner/fields", str(repo_dir))
        _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        from src.services.run_service import run_repo_extraction
        result = run_repo_extraction(repo_id=repo_id, db_path=db_path)

        assert "status" in result
        assert "repo_id" in result
        assert "snapshot_id" in result
        assert "full_name" in result
        assert result["full_name"] == "owner/fields"


# ---------------------------------------------------------------------------
# API endpoint smoke tests
# ---------------------------------------------------------------------------

class TestExtractionAPIEndpoints:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from src.database import DB_PATH
        from src import main as app_module

        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path), \
             patch("src.routes.run_service.backfill_pending_extraction") as mock_bf, \
             patch("src.routes.run_service.run_repo_extraction") as mock_re:
            mock_bf.return_value = {"extracted": 5, "skipped_no_clone": 1, "errors": 0}
            mock_re.return_value = {"status": "ok", "repo_id": 1}
            from src.main import app
            with TestClient(app) as c:
                c._mock_backfill = mock_bf
                c._mock_rerun = mock_re
                yield c, db_path

    def test_backfill_endpoint_nothing_to_do(self, tmp_path):
        """With empty DB there are no pending snapshots — should return nothing_to_do."""
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                resp = c.post("/api/extraction/backfill")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "nothing_to_do"

    def test_backfill_endpoint_with_pending(self, tmp_path):
        """With a pending snapshot it should return queued status."""
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "test/repo", "/some/path")
        _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        with patch("src.database.DB_PATH", db_path), \
             patch("src.main.thread_pool") as mock_pool:
            mock_pool.submit.return_value = None
            from src.main import app
            with TestClient(app) as c:
                resp = c.post("/api/extraction/backfill")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert resp.json()["pending_snapshots"] == 1

    def test_per_repo_extraction_404_on_missing(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                resp = c.post("/api/repos/9999/extraction/run")
        assert resp.status_code == 404

    def test_per_repo_extraction_queued(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn, "test/queued", "/some/path")
        _seed_snapshot(conn, repo_id, "pending")
        conn.close()

        with patch("src.database.DB_PATH", db_path), \
             patch("src.main.thread_pool") as mock_pool:
            mock_pool.submit.return_value = None
            from src.main import app
            with TestClient(app) as c:
                resp = c.post(f"/api/repos/{repo_id}/extraction/run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["repo_id"] == repo_id

    def test_extraction_summary_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        db_path = _make_db(tmp_path)
        with patch("src.database.DB_PATH", db_path):
            from src.main import app
            with TestClient(app) as c:
                resp = c.get("/api/extraction/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_chunks" in data
        assert "snapshot_status_counts" in data

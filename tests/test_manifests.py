"""Tests for repo manifest and chunk manifest/artifact writing."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.classification import Classification
from src.services.extraction import Chunk


def _make_classification(
    band: str = "medium",
    confidence: float = 0.65,
    primary_tag: str = "utilities",
    tags: list[str] | None = None,
    flags: list[str] | None = None,
    summary: str = "python function 'foo' (5 lines) — utilities [medium]",
) -> Classification:
    return Classification(
        category_tags=tags or [primary_tag],
        primary_tag=primary_tag,
        longevity_band=band,
        longevity_confidence=confidence,
        quality_flags=flags or [],
        summary=summary,
    )


def _make_chunk(
    file_path: str = "src/main.py",
    language: str = "python",
    chunk_type: str = "function",
    symbol_name: str = "foo",
    start_line: int = 1,
    end_line: int = 5,
    content: str = "def foo():\n    return 42\n",
) -> Chunk:
    return Chunk(
        file_path=file_path,
        language=language,
        chunk_type=chunk_type,
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        content=content,
    )


class TestRepoManifest:
    def test_writes_manifest_file(self, tmp_path):
        from src.services import manifests as m
        with patch.object(m, "MANIFESTS_REPOS", tmp_path / "manifests" / "repos"):
            path = m.write_repo_manifest(
                repo_id=1,
                snapshot_id=42,
                full_name="owner/repo",
                owner="owner",
                name="repo",
                source_url="https://github.com/owner/repo",
                default_branch="main",
                commit_sha="abc123",
                license="MIT",
                topics=["python", "web"],
                languages=["Python"],
                discovered_via_niche="python-web",
                clone_path="/data/repos/owner_repo",
                ingested_at="2024-01-01T00:00:00",
                ingestion_status="completed",
                extraction_status="pending",
            )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["full_name"] == "owner/repo"
        assert data["commit_sha"] == "abc123"
        assert data["extraction_status"] == "pending"
        assert data["snapshot_id"] == 42

    def test_manifest_all_spec_fields_present(self, tmp_path):
        from src.services import manifests as m
        with patch.object(m, "MANIFESTS_REPOS", tmp_path):
            path = m.write_repo_manifest(
                repo_id=2, snapshot_id=5,
                full_name="a/b", owner="a", name="b",
                source_url="https://github.com/a/b",
                default_branch="main", commit_sha="def456",
                license=None, topics=[], languages=[],
                discovered_via_niche=None,
                clone_path="/data/repos/a_b",
                ingested_at="2024-01-01T00:00:00",
                ingestion_status="completed",
            )
        data = json.loads(path.read_text())
        required_fields = [
            "repo_id", "source_url", "owner", "name", "default_branch",
            "commit_sha", "license", "topics", "languages",
            "discovered_via_niche", "clone_path", "ingested_at",
            "ingestion_status", "extraction_status",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_update_extraction_status(self, tmp_path):
        from src.services import manifests as m
        with patch.object(m, "MANIFESTS_REPOS", tmp_path):
            m.write_repo_manifest(
                repo_id=3, snapshot_id=7,
                full_name="c/d", owner="c", name="d",
                source_url="https://github.com/c/d",
                default_branch="main", commit_sha="ghi789",
                license="MIT", topics=[], languages=[],
                discovered_via_niche=None,
                clone_path="/data/repos/c_d",
                ingested_at="2024-01-01T00:00:00",
                ingestion_status="completed",
                extraction_status="pending",
            )
            m.update_repo_manifest_extraction_status("c/d", 7, "completed", 15, 10)

        manifest_file = next(tmp_path.glob("c_d_7.json"))
        data = json.loads(manifest_file.read_text())
        assert data["extraction_status"] == "completed"
        assert data["extracted_chunks"] == 15
        assert data["inspected_files"] == 10

    def test_update_nonexistent_manifest_is_noop(self, tmp_path):
        from src.services import manifests as m
        with patch.object(m, "MANIFESTS_REPOS", tmp_path):
            # Should not raise
            m.update_repo_manifest_extraction_status("x/y", 999, "completed", 0, 0)


class TestChunkManifest:
    def test_writes_chunk_manifest(self, tmp_path):
        from src.services import manifests as m
        classification = _make_classification()
        chunk = _make_chunk()
        with patch.object(m, "MANIFESTS_CHUNKS", tmp_path / "chunks"):
            path = m.write_chunk_manifest(
                chunk_id=100,
                repo_id=1,
                snapshot_id=42,
                file_path="src/main.py",
                language="python",
                chunk_type="function",
                symbol_name="foo",
                start_line=1,
                end_line=5,
                content_hash=chunk.content_hash,
                classification=classification,
                provenance_ref="owner/repo@42:src/main.py:1-5",
            )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["chunk_id"] == 100
        assert data["file_path"] == "src/main.py"
        assert data["longevity_band"] == "medium"
        assert data["primary_tag"] == "utilities"

    def test_chunk_manifest_has_all_spec_fields(self, tmp_path):
        from src.services import manifests as m
        classification = _make_classification()
        chunk = _make_chunk()
        with patch.object(m, "MANIFESTS_CHUNKS", tmp_path):
            path = m.write_chunk_manifest(
                chunk_id=200, repo_id=1, snapshot_id=42,
                file_path="src/f.py", language="python",
                chunk_type="function", symbol_name="bar",
                start_line=10, end_line=20,
                content_hash=chunk.content_hash,
                classification=classification,
                provenance_ref="owner/repo@42:src/f.py:10-20",
            )
        data = json.loads(path.read_text())
        required = [
            "chunk_id", "repo_id", "file_path", "language", "chunk_type",
            "symbol_name", "start_line", "end_line", "content_hash",
            "category_tags", "longevity_band", "longevity_confidence",
            "summary", "quality_flags", "provenance_ref",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"


class TestCuratedArtifact:
    def test_writes_artifact_to_band_tag_dir(self, tmp_path):
        from src.services import manifests as m
        classification = _make_classification(band="high", primary_tag="api")
        chunk = _make_chunk()
        with patch.object(m, "EXTRACTED_DIR", tmp_path):
            path = m.write_curated_artifact(
                chunk_id=50,
                repo_id=1,
                snapshot_id=42,
                full_name="owner/repo",
                file_path="src/api.py",
                chunk=chunk,
                classification=classification,
            )
        assert path is not None
        assert path.exists()
        # Check it's in the right directory
        assert "high" in str(path)
        assert "api" in str(path)

    def test_artifact_has_provenance_header(self, tmp_path):
        from src.services import manifests as m
        classification = _make_classification()
        chunk = _make_chunk(content="def foo():\n    return 42\n")
        with patch.object(m, "EXTRACTED_DIR", tmp_path):
            path = m.write_curated_artifact(
                chunk_id=51, repo_id=1, snapshot_id=42,
                full_name="owner/repo", file_path="src/main.py",
                chunk=chunk, classification=classification,
            )
        text = path.read_text()
        # Check for provenance JSON block and content
        assert "owner/repo" in text
        assert "def foo()" in text
        assert "---" in text

    def test_invalid_band_falls_back_to_small(self, tmp_path):
        from src.services import manifests as m
        bad_classification = Classification(
            category_tags=["utilities"],
            primary_tag="utilities",
            longevity_band="INVALID",
            longevity_confidence=0.5,
            quality_flags=[],
            summary="test",
        )
        chunk = _make_chunk()
        with patch.object(m, "EXTRACTED_DIR", tmp_path):
            path = m.write_curated_artifact(
                chunk_id=52, repo_id=1, snapshot_id=42,
                full_name="owner/repo", file_path="src/f.py",
                chunk=chunk, classification=bad_classification,
            )
        assert path is not None
        assert "small" in str(path)

    def test_deterministic_filename(self, tmp_path):
        from src.services import manifests as m
        classification = _make_classification()
        chunk = _make_chunk()
        with patch.object(m, "EXTRACTED_DIR", tmp_path):
            p1 = m.write_curated_artifact(
                chunk_id=60, repo_id=1, snapshot_id=42,
                full_name="owner/repo", file_path="src/main.py",
                chunk=chunk, classification=classification,
            )
            p2 = m.write_curated_artifact(
                chunk_id=60, repo_id=1, snapshot_id=42,
                full_name="owner/repo", file_path="src/main.py",
                chunk=chunk, classification=classification,
            )
        assert p1.name == p2.name


class TestDBSchema:
    """Verify new tables and columns exist in the schema."""

    def _get_conn(self):
        import sqlite3
        from src.database import init_db, migrate_db
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        migrate_db(conn)
        return conn

    def test_files_table_exists(self):
        conn = self._get_conn()
        conn.execute("SELECT * FROM files LIMIT 0")

    def test_chunks_table_exists(self):
        conn = self._get_conn()
        conn.execute("SELECT * FROM chunks LIMIT 0")

    def test_chunk_tags_table_exists(self):
        conn = self._get_conn()
        conn.execute("SELECT * FROM chunk_tags LIMIT 0")

    def test_extraction_status_column_on_snapshots(self):
        conn = self._get_conn()
        conn.execute("SELECT extraction_status FROM repo_snapshots LIMIT 0")

    def test_files_columns(self):
        conn = self._get_conn()
        row = conn.execute("PRAGMA table_info(files)").fetchall()
        cols = {r["name"] for r in row}
        required = {
            "file_id", "snapshot_id", "repo_id", "relative_path",
            "language", "file_kind", "size_bytes", "line_count",
            "included", "skip_reason", "inspected_at",
        }
        assert required <= cols

    def test_chunks_columns(self):
        conn = self._get_conn()
        row = conn.execute("PRAGMA table_info(chunks)").fetchall()
        cols = {r["name"] for r in row}
        required = {
            "chunk_id", "file_id", "snapshot_id", "repo_id",
            "chunk_type", "symbol_name", "start_line", "end_line",
            "content_hash", "language", "longevity_band",
            "longevity_confidence", "primary_tag", "summary",
            "quality_flags", "extracted_at",
        }
        assert required <= cols

    def test_chunk_tags_columns(self):
        conn = self._get_conn()
        row = conn.execute("PRAGMA table_info(chunk_tags)").fetchall()
        cols = {r["name"] for r in row}
        assert {"tag_id", "chunk_id", "tag", "tag_source"} <= cols

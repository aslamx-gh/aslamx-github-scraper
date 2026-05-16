"""Tests for V2 Phase 6: deterministic teaching artifact generation."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers shared with validation tests (inlined to keep tests self-contained)
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


def _seed_file(conn: sqlite3.Connection, snapshot_id: int, repo_id: int,
               relative_path: str = "src/main.py") -> int:
    cursor = conn.execute(
        """INSERT INTO files (snapshot_id, repo_id, relative_path, language, file_kind,
           size_bytes, line_count, included, inspected_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, repo_id, relative_path, "python", "source", 500, 40, 1, _now()),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_chunk(
    conn: sqlite3.Connection,
    snapshot_id: int,
    repo_id: int,
    file_id: int,
    *,
    content_hash: str = "hash_001",
    validation_status: str = "accepted",
    difficulty: str = "intermediate",
    topic: str = "functions",
    subtopic: str | None = None,
    paradigm: str = "imperative",
    chunk_type: str = "function",
    symbol_name: str = "my_func",
) -> int:
    cursor = conn.execute(
        """INSERT INTO chunks
           (file_id, snapshot_id, repo_id, chunk_type, symbol_name,
            start_line, end_line, content_hash, language,
            longevity_band, longevity_confidence, primary_tag, summary,
            quality_flags, extracted_at, validation_status,
            difficulty, topic, subtopic, paradigm, style, architecture_level,
            security_relevance, example_type, beginner_safe,
            chunk_quality_score, teaching_value_score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            file_id, snapshot_id, repo_id, chunk_type, symbol_name,
            1, 20, content_hash, "python",
            "medium", 0.8, "utilities", "Function my_func",
            "[]", _now(), validation_status,
            difficulty, topic, subtopic, paradigm, "procedural", "function",
            "none", "good_example", "safe",
            0.7, 0.75,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Core generation behaviour
# ---------------------------------------------------------------------------

class TestGenerateTeachingArtifacts:
    def test_generates_artifact_for_accepted_chunk(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        file_id = _seed_file(conn, snap_id, repo_id)
        chunk_id = _seed_chunk(conn, snap_id, repo_id, file_id,
                               content_hash="teach_h001")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            report = generate_teaching_artifacts(db_path=db_path)

        assert report.generated == 1
        assert report.eligible_count == 1
        assert report.skipped_existing == 0
        assert not report.errors

        artifact_file = teaching_dir / f"chunk_{chunk_id}.json"
        assert artifact_file.exists()

    def test_skips_quarantined_chunks(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        file_id = _seed_file(conn, snap_id, repo_id)
        _seed_chunk(conn, snap_id, repo_id, file_id,
                    content_hash="quar_h001", validation_status="quarantined")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            report = generate_teaching_artifacts(db_path=db_path)

        assert report.eligible_count == 0
        assert report.generated == 0

    def test_skips_pending_chunks(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        file_id = _seed_file(conn, snap_id, repo_id)
        _seed_chunk(conn, snap_id, repo_id, file_id,
                    content_hash="pend_h001", validation_status="pending")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            report = generate_teaching_artifacts(db_path=db_path)

        assert report.eligible_count == 0

    def test_scoped_to_snapshot(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_a = _seed_snapshot(conn, repo_id)
        snap_b = _seed_snapshot(conn, repo_id)
        file_a = _seed_file(conn, snap_a, repo_id, "src/a.py")
        file_b = _seed_file(conn, snap_b, repo_id, "src/b.py")
        _seed_chunk(conn, snap_a, repo_id, file_a, content_hash="h_scope_a")
        _seed_chunk(conn, snap_b, repo_id, file_b, content_hash="h_scope_b")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            report = generate_teaching_artifacts(db_path=db_path, snapshot_id=snap_a)

        assert report.eligible_count == 1
        assert report.generated == 1

    def test_nothing_to_do_on_empty_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            report = generate_teaching_artifacts(db_path=db_path)
        assert report.eligible_count == 0
        assert report.generated == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_skips_existing_artifacts(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        file_id = _seed_file(conn, snap_id, repo_id)
        _seed_chunk(conn, snap_id, repo_id, file_id, content_hash="idem_h001")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            r1 = generate_teaching_artifacts(db_path=db_path)
            r2 = generate_teaching_artifacts(db_path=db_path)

        assert r1.generated == 1
        assert r2.generated == 0
        assert r2.skipped_existing == 1

    def test_multiple_chunks_all_generated(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        for i in range(4):
            file_id = _seed_file(conn, snap_id, repo_id, f"src/mod_{i}.py")
            _seed_chunk(conn, snap_id, repo_id, file_id,
                        content_hash=f"multi_teach_{i:03d}")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            report = generate_teaching_artifacts(db_path=db_path)

        assert report.generated == 4
        assert len(list(teaching_dir.glob("chunk_*.json"))) == 4


# ---------------------------------------------------------------------------
# Artifact content and provenance
# ---------------------------------------------------------------------------

class TestArtifactContent:
    def _get_artifact(self, tmp_path, difficulty="intermediate",
                      topic="functions", paradigm="imperative",
                      chunk_type="function", symbol_name="test_fn") -> dict:
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        file_id = _seed_file(conn, snap_id, repo_id, "src/service.py")
        chunk_id = _seed_chunk(
            conn, snap_id, repo_id, file_id,
            content_hash="art_content_hash",
            difficulty=difficulty, topic=topic, paradigm=paradigm,
            chunk_type=chunk_type, symbol_name=symbol_name,
        )
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.teaching_artifacts import generate_teaching_artifacts
            generate_teaching_artifacts(db_path=db_path)

        return json.loads((teaching_dir / f"chunk_{chunk_id}.json").read_text())

    def test_provenance_fields_present(self, tmp_path):
        data = self._get_artifact(tmp_path)
        assert "chunk_id" in data
        assert "repo_id" in data
        assert "snapshot_id" in data
        assert "content_hash" in data
        assert data["content_hash"] == "art_content_hash"

    def test_teaching_fields_present(self, tmp_path):
        data = self._get_artifact(tmp_path)
        assert "explanation_seed" in data
        assert "prerequisite_hints" in data
        assert "exercise_prompt" in data
        assert "generated_at" in data

    def test_explanation_seed_contains_symbol(self, tmp_path):
        data = self._get_artifact(tmp_path, symbol_name="compute_score")
        assert "compute_score" in data["explanation_seed"]

    def test_explanation_seed_contains_file(self, tmp_path):
        data = self._get_artifact(tmp_path)
        assert "src/service.py" in data["explanation_seed"]

    def test_prerequisite_hints_is_list(self, tmp_path):
        data = self._get_artifact(tmp_path)
        assert isinstance(data["prerequisite_hints"], list)
        assert len(data["prerequisite_hints"]) >= 1

    def test_prerequisite_hints_reflect_topic(self, tmp_path):
        data = self._get_artifact(tmp_path, topic="testing")
        hints = data["prerequisite_hints"]
        assert any("test" in h.lower() or "assert" in h.lower() or "pytest" in h.lower()
                   for h in hints)

    def test_exercise_prompt_is_string(self, tmp_path):
        data = self._get_artifact(tmp_path)
        assert isinstance(data["exercise_prompt"], str)
        assert len(data["exercise_prompt"]) > 10

    def test_exercise_prompt_difficulty_beginner(self, tmp_path):
        data = self._get_artifact(tmp_path, difficulty="beginner")
        # Beginner prompts are simpler tasks
        prompt = data["exercise_prompt"].lower()
        assert any(word in prompt for word in ["trace", "docstring", "call", "print", "write"])

    def test_exercise_prompt_difficulty_advanced(self, tmp_path):
        data = self._get_artifact(tmp_path, difficulty="advanced")
        prompt = data["exercise_prompt"].lower()
        assert any(word in prompt for word in ["refactor", "analyse", "analyze", "extend", "complexity"])

    def test_determinism_same_chunk_same_output(self, tmp_path):
        """Same chunk metadata must always produce the same artifact content."""
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        repo_id = _seed_repo(conn)
        snap_id = _seed_snapshot(conn, repo_id)
        file_id = _seed_file(conn, snap_id, repo_id, "src/determ.py")
        chunk_id = _seed_chunk(conn, snap_id, repo_id, file_id,
                               content_hash="determ_hash")
        conn.close()

        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        from src.services.teaching_artifacts import generate_teaching_artifacts, _build_artifact

        # Build artifact twice from the same DB row
        conn2 = sqlite3.connect(str(db_path))
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            """SELECT c.*, f.relative_path AS file_path FROM chunks c
               JOIN files f ON f.file_id = c.file_id WHERE c.chunk_id=?""",
            (chunk_id,),
        ).fetchone()
        conn2.close()

        a1 = _build_artifact(row)
        a2 = _build_artifact(row)

        assert a1.explanation_seed == a2.explanation_seed
        assert a1.prerequisite_hints == a2.prerequisite_hints
        assert a1.exercise_prompt == a2.exercise_prompt


# ---------------------------------------------------------------------------
# write_teaching_sidecar (manifests integration)
# ---------------------------------------------------------------------------

class TestWriteTeachingSidecar:
    def test_writes_json_file(self, tmp_path):
        from src.services.teaching_artifacts import TeachingArtifact
        artifact = TeachingArtifact(
            chunk_id=99,
            repo_id=1,
            snapshot_id=2,
            content_hash="sidecar_test_hash",
            explanation_seed="Function `foo` in `src/foo.py`",
            prerequisite_hints=["functions", "return values"],
            exercise_prompt="Write a test for this function.",
            generated_at=_now(),
        )
        teaching_dir = tmp_path / "teaching"
        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.manifests import write_teaching_sidecar
            path = write_teaching_sidecar(artifact)

        assert path == teaching_dir / "chunk_99.json"
        data = json.loads(path.read_text())
        assert data["chunk_id"] == 99
        assert data["explanation_seed"] == "Function `foo` in `src/foo.py`"
        assert data["prerequisite_hints"] == ["functions", "return values"]

    def test_creates_directory_if_missing(self, tmp_path):
        from src.services.teaching_artifacts import TeachingArtifact
        artifact = TeachingArtifact(
            chunk_id=42, repo_id=1, snapshot_id=1,
            content_hash="dir_create_hash",
            explanation_seed="seed", prerequisite_hints=[], exercise_prompt="do it",
            generated_at=_now(),
        )
        teaching_dir = tmp_path / "nested" / "teaching"
        assert not teaching_dir.exists()

        from src.services import manifests as m
        with patch.object(m, "TEACHING_DIR", teaching_dir):
            from src.services.manifests import write_teaching_sidecar
            write_teaching_sidecar(artifact)

        assert teaching_dir.exists()
        assert (teaching_dir / "chunk_42.json").exists()

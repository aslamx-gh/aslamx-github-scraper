"""Tests for V2 Phase 2: repo quality scoring, filesystem inspection, and quality filtering."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.github.client import score_repo_quality_from_api
from src.services.filtering import filter_candidate, get_filter_settings
from src.services.discovery import _attach_quality_signals, _parse_repo_input
from src.database import get_connection, init_db, seed_defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db() -> sqlite3.Connection:
    tmp = tempfile.mktemp(suffix=".db")
    conn = get_connection(Path(tmp))
    init_db(conn)
    seed_defaults(conn)
    return conn


def _make_repo(**kwargs) -> dict:
    defaults = {
        "full_name": "owner/repo",
        "owner": "owner",
        "name": "repo",
        "source_url": "https://github.com/owner/repo",
        "default_branch": "main",
        "license": "MIT",
        "topics": ["python", "api", "backend"],
        "languages": ["python"],
        "size_kb": 5000,
        "stars": 150,
        "is_fork": False,
        "is_archived": False,
        "last_pushed_at": "2025-12-01T00:00:00Z",
        "description": "A solid Python backend library with good patterns.",
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# score_repo_quality_from_api
# ---------------------------------------------------------------------------

class TestScoreRepoQualityFromApi:
    def test_well_rounded_repo_scores_high(self):
        repo = _make_repo(
            description="Excellent documented Python library",
            topics=["python", "api", "rest", "backend"],
            license="MIT",
            stars=1000,
            size_kb=25000,
        )
        signals = score_repo_quality_from_api(repo)
        assert signals["quality_score"] >= 0.75
        assert signals["has_description"] is True
        assert signals["has_topics"] is True
        assert signals["has_license"] is True

    def test_no_description_loses_points(self):
        full_repo = _make_repo(description="A great repo", stars=100)
        empty_repo = _make_repo(description="", stars=100)
        full_signals = score_repo_quality_from_api(full_repo)
        empty_signals = score_repo_quality_from_api(empty_repo)
        assert full_signals["quality_score"] > empty_signals["quality_score"]
        assert empty_signals["has_description"] is False

    def test_no_topics_loses_points(self):
        with_topics = _make_repo(topics=["python", "web"])
        without_topics = _make_repo(topics=[])
        s_with = score_repo_quality_from_api(with_topics)
        s_without = score_repo_quality_from_api(without_topics)
        assert s_with["quality_score"] > s_without["quality_score"]
        assert s_without["has_topics"] is False

    def test_no_license_loses_points(self):
        licensed = _make_repo(license="MIT")
        unlicensed = _make_repo(license=None)
        s_l = score_repo_quality_from_api(licensed)
        s_u = score_repo_quality_from_api(unlicensed)
        assert s_l["quality_score"] > s_u["quality_score"]
        assert s_u["has_license"] is False

    def test_zero_stars_loses_points(self):
        popular = _make_repo(stars=500)
        unknown = _make_repo(stars=0)
        s_p = score_repo_quality_from_api(popular)
        s_u = score_repo_quality_from_api(unknown)
        assert s_p["quality_score"] > s_u["quality_score"]

    def test_500_plus_stars_gets_full_star_credit(self):
        r500 = _make_repo(stars=500)
        r5000 = _make_repo(stars=5000)
        s500 = score_repo_quality_from_api(r500)
        s5000 = score_repo_quality_from_api(r5000)
        # Both get the same star credit (capped)
        assert abs(s500["quality_score"] - s5000["quality_score"]) < 0.01

    def test_trivially_tiny_repo_loses_points(self):
        normal = _make_repo(size_kb=5000)
        tiny = _make_repo(size_kb=5)
        s_n = score_repo_quality_from_api(normal)
        s_t = score_repo_quality_from_api(tiny)
        assert s_n["quality_score"] > s_t["quality_score"]
        assert s_t["not_trivially_tiny"] is False

    def test_oversized_repo_loses_points(self):
        normal = _make_repo(size_kb=50000)
        huge = _make_repo(size_kb=300000)
        s_n = score_repo_quality_from_api(normal)
        s_h = score_repo_quality_from_api(huge)
        assert s_n["quality_score"] > s_h["quality_score"]
        assert s_h["not_oversized"] is False

    def test_score_is_capped_at_1(self):
        perfect = _make_repo(
            description="Perfect library",
            topics=["a", "b", "c", "d", "e"],
            license="MIT",
            stars=5000,
            size_kb=50000,
        )
        signals = score_repo_quality_from_api(perfect)
        assert signals["quality_score"] <= 1.0

    def test_score_is_non_negative(self):
        bare = _make_repo(description="", topics=[], license=None, stars=0, size_kb=2)
        signals = score_repo_quality_from_api(bare)
        assert signals["quality_score"] >= 0.0

    def test_topics_count_bonus_capped_at_3(self):
        three_topics = _make_repo(topics=["a", "b", "c"])
        ten_topics = _make_repo(topics=["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"])
        s3 = score_repo_quality_from_api(three_topics)
        s10 = score_repo_quality_from_api(ten_topics)
        # Both get max topics bonus (capped at 3 extra)
        assert abs(s3["quality_score"] - s10["quality_score"]) < 0.01


# ---------------------------------------------------------------------------
# _attach_quality_signals
# ---------------------------------------------------------------------------

class TestAttachQualitySignals:
    def test_quality_score_attached_to_repo_dict(self):
        repo = _make_repo()
        _attach_quality_signals(repo)
        assert "_quality_score" in repo
        assert "_quality_signals" in repo
        assert isinstance(repo["_quality_score"], float)

    def test_quality_signals_contains_expected_keys(self):
        repo = _make_repo()
        _attach_quality_signals(repo)
        sigs = repo["_quality_signals"]
        for key in ("has_description", "has_topics", "has_license", "stars", "quality_score"):
            assert key in sigs, f"Missing signal key: {key}"

    def test_attach_does_not_mutate_other_fields(self):
        repo = _make_repo(full_name="a/b", stars=42)
        _attach_quality_signals(repo)
        assert repo["full_name"] == "a/b"
        assert repo["stars"] == 42


# ---------------------------------------------------------------------------
# filter_candidate — teaching quality gate
# ---------------------------------------------------------------------------

class TestFilterCandidateQualityGate:
    def _settings(self, min_score=0.0):
        return {
            "require_license": False,
            "exclude_forks": False,
            "exclude_archived": False,
            "max_repo_size_kb": 999999,
            "min_stars": 0,
            "min_recent_activity_days": 0,
            "quality.min_score": min_score,
        }

    def test_quality_gate_disabled_by_default(self):
        repo = _make_repo()
        repo["_quality_score"] = 0.01  # Very low quality
        accepted, code, _ = filter_candidate(repo, None, self._settings(min_score=0.0))
        assert accepted is True

    def test_repo_above_threshold_is_accepted(self):
        repo = _make_repo()
        repo["_quality_score"] = 0.70
        accepted, code, _ = filter_candidate(repo, None, self._settings(min_score=0.50))
        assert accepted is True

    def test_repo_below_threshold_is_rejected(self):
        repo = _make_repo()
        repo["_quality_score"] = 0.30
        accepted, code, explanation = filter_candidate(repo, None, self._settings(min_score=0.50))
        assert accepted is False
        assert code == "below_teaching_quality"
        assert "0.300" in explanation

    def test_missing_quality_score_is_not_rejected(self):
        """Repos without a _quality_score (e.g. pre-V2 path) should not be rejected."""
        repo = _make_repo()
        # No _quality_score key attached
        accepted, code, _ = filter_candidate(repo, None, self._settings(min_score=0.50))
        assert accepted is True

    def test_niche_can_override_min_quality_higher(self):
        repo = _make_repo()
        repo["_quality_score"] = 0.55
        settings = self._settings(min_score=0.30)
        niche = {"min_repo_quality_score": 0.70}
        accepted, code, _ = filter_candidate(repo, niche, settings)
        assert accepted is False
        assert code == "below_teaching_quality"

    def test_niche_can_override_min_quality_lower(self):
        repo = _make_repo()
        repo["_quality_score"] = 0.40
        settings = self._settings(min_score=0.60)
        niche = {"min_repo_quality_score": 0.20}
        accepted, code, _ = filter_candidate(repo, niche, settings)
        assert accepted is True

    def test_niche_without_quality_field_uses_global(self):
        repo = _make_repo()
        repo["_quality_score"] = 0.30
        settings = self._settings(min_score=0.50)
        niche = {}  # No min_repo_quality_score
        accepted, code, _ = filter_candidate(repo, niche, settings)
        assert accepted is False

    def test_quality_check_runs_after_standard_filters(self):
        """Standard filters (fork, license) still apply; quality check is additive."""
        repo = _make_repo(is_fork=True)
        repo["_quality_score"] = 0.90
        settings = self._settings(min_score=0.0)
        settings["exclude_forks"] = True
        accepted, code, _ = filter_candidate(repo, None, settings)
        assert accepted is False
        assert code == "fork_excluded"


# ---------------------------------------------------------------------------
# Discovery settings wiring
# ---------------------------------------------------------------------------

class TestDiscoverySettingsWiring:
    def test_max_pages_default_in_db(self):
        conn = _fresh_db()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='discovery.max_pages'"
        ).fetchone()
        assert row is not None
        assert int(row["value"]) == 3

    def test_results_per_page_default_in_db(self):
        conn = _fresh_db()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='discovery.results_per_page'"
        ).fetchone()
        assert row is not None
        assert int(row["value"]) == 30

    def test_quality_min_score_default_is_zero(self):
        conn = _fresh_db()
        row = conn.execute(
            "SELECT value FROM settings WHERE key='discovery.quality.min_score'"
        ).fetchone()
        assert row is not None
        assert float(row["value"]) == 0.0

    def test_discovery_settings_readable_by_helper(self):
        from src.services.run_service import _get_discovery_settings
        conn = _fresh_db()
        settings = _get_discovery_settings(conn)
        assert settings.get("max_pages") == 3
        assert settings.get("results_per_page") == 30


# ---------------------------------------------------------------------------
# Filesystem quality inspection
# ---------------------------------------------------------------------------

class TestFilesystemQualityInspection:
    def _make_clone(self, tmp_path: Path, structure: dict) -> Path:
        """Create a fake clone directory from a structure dict {name: is_dir}."""
        for name, is_dir in structure.items():
            p = tmp_path / name
            if is_dir:
                p.mkdir(parents=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("content")
        return tmp_path

    def test_has_docs_detected_from_docs_dir(self, tmp_path):
        from src.services.run_service import _detect_has_docs
        clone = self._make_clone(tmp_path, {"docs": True, "src": True})
        assert _detect_has_docs(clone) is True

    def test_has_docs_detected_from_readme(self, tmp_path):
        from src.services.run_service import _detect_has_docs
        clone = self._make_clone(tmp_path, {"README.md": False, "src": True})
        assert _detect_has_docs(clone) is True

    def test_no_docs_returns_false(self, tmp_path):
        from src.services.run_service import _detect_has_docs
        clone = self._make_clone(tmp_path, {"src": True, "main.py": False})
        assert _detect_has_docs(clone) is False

    def test_has_tests_detected_from_tests_dir(self, tmp_path):
        from src.services.run_service import _detect_has_tests
        clone = self._make_clone(tmp_path, {"tests": True})
        assert _detect_has_tests(clone) is True

    def test_has_tests_false_without_test_dir(self, tmp_path):
        from src.services.run_service import _detect_has_tests
        clone = self._make_clone(tmp_path, {"src": True, "main.py": False})
        assert _detect_has_tests(clone) is False

    def test_has_examples_detected(self, tmp_path):
        from src.services.run_service import _detect_has_examples
        clone = self._make_clone(tmp_path, {"examples": True})
        assert _detect_has_examples(clone) is True

    def test_has_examples_false_without_examples_dir(self, tmp_path):
        from src.services.run_service import _detect_has_examples
        clone = self._make_clone(tmp_path, {"src": True})
        assert _detect_has_examples(clone) is False

    def test_maintenance_health_active_with_github_dir(self, tmp_path):
        from src.services.run_service import _estimate_maintenance_health
        (tmp_path / ".github").mkdir()
        assert _estimate_maintenance_health(tmp_path) == "active"

    def test_maintenance_health_unknown_bare_repo(self, tmp_path):
        from src.services.run_service import _estimate_maintenance_health
        (tmp_path / "main.py").write_text("x = 1")
        assert _estimate_maintenance_health(tmp_path) == "unknown"

    def test_generated_signal_zero_no_markers(self, tmp_path):
        from src.services.run_service import _estimate_generated_code_signal
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        sig = _estimate_generated_code_signal(tmp_path)
        assert sig == 0.0

    def test_generated_signal_nonzero_with_marker(self, tmp_path):
        from src.services.run_service import _estimate_generated_code_signal
        (tmp_path / "gen.py").write_text("# This file was generated by protoc\nx = 1\n")
        sig = _estimate_generated_code_signal(tmp_path)
        assert sig > 0.0

    def test_update_repo_filesystem_quality_writes_to_db(self, tmp_path):
        from src.services.run_service import update_repo_filesystem_quality
        conn = _fresh_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("o", "r", "o/r", "https://github.com/o/r", now, now),
        )
        conn.commit()
        repo_id = conn.execute("SELECT repo_id FROM repos WHERE full_name='o/r'").fetchone()["repo_id"]

        (tmp_path / "docs").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / ".github").mkdir()

        update_repo_filesystem_quality(conn, repo_id, tmp_path)

        row = conn.execute(
            "SELECT has_docs, has_tests, has_examples, maintenance_health FROM repos WHERE repo_id=?",
            (repo_id,),
        ).fetchone()
        assert row["has_docs"] == 1
        assert row["has_tests"] == 1
        assert row["has_examples"] == 0
        assert row["maintenance_health"] == "active"

    def test_update_repo_filesystem_quality_nonfatal_on_missing_path(self, tmp_path):
        """Should not raise even if the clone path doesn't exist."""
        from src.services.run_service import update_repo_filesystem_quality
        conn = _fresh_db()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("o", "r2", "o/r2", "https://github.com/o/r2", now, now),
        )
        conn.commit()
        repo_id = conn.execute("SELECT repo_id FROM repos WHERE full_name='o/r2'").fetchone()["repo_id"]
        bad_path = tmp_path / "nonexistent"
        # Should not raise
        update_repo_filesystem_quality(conn, repo_id, bad_path)

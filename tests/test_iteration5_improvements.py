"""Tests for Iteration 5 improvements: resilience, niche flexibility, settings, run detail."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.database import get_connection, init_db, migrate_db, seed_defaults, DEFAULT_SETTINGS
from src.services import run_service
from src.services.filtering import filter_candidate, record_rejection
from src.services.discovery import discover_from_niches


# ---------------------------------------------------------------------------
# Item 3 — Run execution resilience
# ---------------------------------------------------------------------------

class TestRunExecutionResilience(unittest.TestCase):
    """Verify that a per-repo error doesn't collapse the whole run."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        migrate_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_manual_run_continues_after_per_repo_error(self):
        """If one repo raises unexpectedly, the run should continue and record a failure."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        # Simulate a resolved list where one item triggers an unexpected error
        # by patching filter_candidate to raise for one specific repo
        call_count = [0]

        def fake_filter(repo, niche, settings):
            call_count[0] += 1
            if repo.get("full_name") == "owner/bad":
                raise RuntimeError("Simulated unexpected error")
            return True, None, None

        resolved = [
            ({"full_name": "owner/good"}, "owner/good"),
            ({"full_name": "owner/bad"}, "owner/bad"),
        ]
        creds = MagicMock()
        client = MagicMock()

        with patch("src.services.run_service.filter_candidate", side_effect=fake_filter):
            with patch("src.services.run_service.resolve_manual_inputs", return_value=resolved):
                with patch("src.services.run_service._clone_and_record"):
                    run_service._execute_manual_run(
                        self.conn, client, creds, run_id, ["owner/good", "owner/bad"],
                        {}
                    )

        # Both repos were attempted (filter called for both)
        self.assertEqual(call_count[0], 2)

        # A failure should be recorded for owner/bad
        failures = run_service.get_run_failures(self.conn, run_id)
        error_types = [f["error_type"] for f in failures]
        self.assertIn("processing_error", error_types)

    def test_niche_run_continues_after_per_repo_error(self):
        """If one repo raises unexpectedly in niche run, run continues."""
        run_id = run_service.create_run(self.conn, "niche_group")

        niche = {"niche_id": "test-niche"}
        candidates = [
            ({"full_name": "owner/good"}, niche, "q1"),
            ({"full_name": "owner/bad"}, niche, "q2"),
        ]
        creds = MagicMock()
        client = MagicMock()

        call_count = [0]

        def fake_filter(repo, niche, settings):
            call_count[0] += 1
            if repo.get("full_name") == "owner/bad":
                raise RuntimeError("Simulated niche error")
            return True, None, None

        with patch("src.services.run_service.filter_candidate", side_effect=fake_filter):
            with patch("src.services.run_service.discover_from_niches", return_value=candidates):
                with patch("src.services.run_service._clone_and_record"):
                    run_service._execute_niche_run(
                        self.conn, client, creds, run_id, ["test-niche"], {}
                    )

        self.assertEqual(call_count[0], 2)
        failures = run_service.get_run_failures(self.conn, run_id)
        error_types = [f["error_type"] for f in failures]
        self.assertIn("processing_error", error_types)


# ---------------------------------------------------------------------------
# Item 4 — Niche selection flexibility
# ---------------------------------------------------------------------------

class TestExcludeTermsFiltering(unittest.TestCase):
    """Verify exclude_terms are checked in filter_candidate."""

    def _make_repo(self, full_name, description=None):
        return {
            "full_name": full_name,
            "description": description or "",
            "is_fork": False,
            "is_archived": False,
            "license": "MIT",
            "size_kb": 1000,
            "stars": 10,
            "languages": ["Python"],
            "last_pushed_at": "2025-01-01T00:00:00Z",
        }

    def test_exclude_term_in_repo_name_rejected(self):
        niche = {"exclude_terms": ["tutorial"]}
        repo = self._make_repo("owner/python-tutorial-basics")
        accepted, reason, explanation = filter_candidate(repo, niche, {})
        self.assertFalse(accepted)
        self.assertEqual(reason, "exclude_term_match")
        self.assertIn("tutorial", explanation)

    def test_exclude_term_in_description_rejected(self):
        niche = {"exclude_terms": ["deprecated"]}
        repo = self._make_repo("owner/somerepo", description="This project is deprecated")
        accepted, reason, _ = filter_candidate(repo, niche, {})
        self.assertFalse(accepted)
        self.assertEqual(reason, "exclude_term_match")

    def test_exclude_term_case_insensitive(self):
        niche = {"exclude_terms": ["Demo"]}
        repo = self._make_repo("owner/awesome-demo")
        accepted, reason, _ = filter_candidate(repo, niche, {})
        self.assertFalse(accepted)
        self.assertEqual(reason, "exclude_term_match")

    def test_no_exclude_terms_passes(self):
        niche = {"exclude_terms": []}
        repo = self._make_repo("owner/clean-repo")
        accepted, _, _ = filter_candidate(repo, niche, {})
        self.assertTrue(accepted)

    def test_exclude_terms_json_string_parsed(self):
        """exclude_terms stored as JSON string (from DB) should still work."""
        niche = {"exclude_terms": '["fork", "mirror"]'}
        repo = self._make_repo("owner/my-mirror-repo")
        accepted, reason, _ = filter_candidate(repo, niche, {})
        self.assertFalse(accepted)
        self.assertEqual(reason, "exclude_term_match")

    def test_no_niche_exclude_terms_ignored(self):
        """No niche means no exclude_terms check applied."""
        repo = self._make_repo("owner/tutorial-demo")
        accepted, _, _ = filter_candidate(repo, None, {})
        self.assertTrue(accepted)


class TestTopicDiscovery(unittest.TestCase):
    """Verify github_topics are used in discovery."""

    def test_topic_query_added_to_candidates(self):
        """discover_from_niches should also search by topic."""
        tmpdir = tempfile.mkdtemp()
        conn = get_connection(Path(tmpdir) / "test.db")
        init_db(conn)
        migrate_db(conn)
        seed_defaults(conn)

        # Insert a niche with a topic but no search queries
        conn.execute(
            """INSERT INTO niches (niche_id, title, github_search_queries, github_topics,
               exclude_terms, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test-topic-niche", "Topic Niche", "[]", '["fastapi"]', "[]", 1,
             "2024-01-01", "2024-01-01"),
        )
        conn.commit()

        search_calls = []

        def fake_search(query):
            search_calls.append(query)
            return [{"full_name": f"owner/{query.replace(':', '-')}", "stars": 10}]

        client = MagicMock()
        client.search_repos.side_effect = fake_search

        candidates = discover_from_niches(client, conn, ["test-topic-niche"])

        # Should have called search with "topic:fastapi"
        self.assertIn("topic:fastapi", search_calls)
        full_names = [c[0]["full_name"] for c in candidates]
        self.assertTrue(any("topic" in fn for fn in full_names))
        conn.close()

    def test_topic_deduplication(self):
        """Same repo from topic search and query search should only appear once."""
        tmpdir = tempfile.mkdtemp()
        conn = get_connection(Path(tmpdir) / "test.db")
        init_db(conn)
        migrate_db(conn)
        seed_defaults(conn)

        conn.execute(
            """INSERT INTO niches (niche_id, title, github_search_queries, github_topics,
               exclude_terms, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("dup-niche", "Dup Niche", '["language:python"]', '["python"]', "[]", 1,
             "2024-01-01", "2024-01-01"),
        )
        conn.commit()

        def fake_search(query):
            # Both queries return the same repo
            return [{"full_name": "owner/shared-repo", "stars": 5}]

        client = MagicMock()
        client.search_repos.side_effect = fake_search

        candidates = discover_from_niches(client, conn, ["dup-niche"])
        full_names = [c[0]["full_name"] for c in candidates]
        self.assertEqual(full_names.count("owner/shared-repo"), 1)
        conn.close()


# ---------------------------------------------------------------------------
# Item 5 — Settings defaults and presets
# ---------------------------------------------------------------------------

class TestImprovedDefaults(unittest.TestCase):
    """Verify DEFAULT_SETTINGS reflect improved conservative defaults."""

    def test_max_repo_size_default_is_reasonable(self):
        """Default max repo size should not be 512MB (too permissive)."""
        size_kb = int(DEFAULT_SETTINGS.get("filter.max_repo_size_kb", 0))
        self.assertLessEqual(size_kb, 200000, "Default max size too large — increases clone failures")

    def test_min_stars_default_is_positive(self):
        """Default min_stars should filter obviously low-quality repos."""
        min_stars = int(DEFAULT_SETTINGS.get("filter.min_stars", 0))
        self.assertGreater(min_stars, 0, "Default min_stars should be > 0 for quality signal")

    def test_min_activity_days_has_a_default(self):
        """Default activity days should filter stale repos."""
        activity_days = int(DEFAULT_SETTINGS.get("filter.min_recent_activity_days", 0))
        self.assertGreater(activity_days, 0, "Default activity days should be > 0 to filter dead repos")


class TestFilterPresetJSFunction(unittest.TestCase):
    """Verify applyFilterPreset function exists in app.js."""

    def test_apply_filter_preset_exists_in_js(self):
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
        src = js_path.read_text()
        self.assertIn("function applyFilterPreset", src)

    def test_preset_types_defined(self):
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
        src = js_path.read_text()
        self.assertIn("permissive", src)
        self.assertIn("standard", src)
        self.assertIn("strict", src)

    def test_settings_template_has_preset_buttons(self):
        tpl = Path(__file__).resolve().parent.parent / "templates" / "settings.html"
        src = tpl.read_text()
        self.assertIn("applyFilterPreset", src)
        self.assertIn("Permissive", src)
        self.assertIn("Standard", src)
        self.assertIn("Strict", src)


# ---------------------------------------------------------------------------
# Item 7 — Run detail performance panel
# ---------------------------------------------------------------------------

class TestRunDetailPerformancePanel(unittest.TestCase):
    """Verify run_detail.html has the performance panel."""

    def test_performance_section_exists(self):
        tpl = Path(__file__).resolve().parent.parent / "templates" / "run_detail.html"
        src = tpl.read_text()
        self.assertIn("Performance", src)

    def test_elapsed_time_rendered(self):
        tpl = Path(__file__).resolve().parent.parent / "templates" / "run_detail.html"
        src = tpl.read_text()
        self.assertIn("elapsed_seconds", src)
        self.assertIn("Elapsed", src)

    def test_success_rate_rendered(self):
        tpl = Path(__file__).resolve().parent.parent / "templates" / "run_detail.html"
        src = tpl.read_text()
        self.assertIn("completion_pct", src)
        self.assertIn("Success rate", src)

    def test_clone_paths_section_exists(self):
        tpl = Path(__file__).resolve().parent.parent / "templates" / "run_detail.html"
        src = tpl.read_text()
        self.assertIn("item_clone_paths", src)
        self.assertIn("Clone Path", src)

    def test_run_detail_route_computes_elapsed(self):
        """routes.py ui_run_detail should compute elapsed_seconds."""
        routes_path = Path(__file__).resolve().parent.parent / "src" / "routes.py"
        src = routes_path.read_text()
        self.assertIn("elapsed_seconds", src)
        self.assertIn("item_clone_paths", src)


if __name__ == "__main__":
    unittest.main()

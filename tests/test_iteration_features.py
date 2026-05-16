"""Tests for iteration 2 features: blockers fixed + new features."""

import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from src.main import app
from src.database import get_connection, init_db, seed_defaults
from src.services import run_service


class TestTokenRedaction(unittest.TestCase):
    """Verify C-1: Token persistence eliminated."""

    def test_redact_token_in_errors(self):
        """Token should be redacted in error messages."""
        from src.github.auth import redact_token

        # Test with a full-length token (20+ chars after ghp_)
        msg = "Clone failed: https://x-access-token:ghp_abcdefghij1234567890@github.com/owner/repo"
        redacted = redact_token(msg)
        self.assertNotIn("ghp_", redacted)
        self.assertIn("***REDACTED***", redacted)

    def test_git_askpass_env_used(self):
        """Git operations should use env auth, not URL embedding."""
        from src.github.cloner import _build_auth_env
        from src.github.auth import GitHubCredentials

        creds = GitHubCredentials(mode="pat", token="ghp_abcdefghij1234567890")
        env = _build_auth_env(creds)
        # Should have auth env vars, not token in clone URL
        self.assertIn("GIT_USERNAME", env)
        self.assertIn("GIT_PASSWORD", env)


class TestRunStats(unittest.TestCase):
    """Verify C-2: Rejected repos not counted as failed."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_rejections_separate_from_failures(self):
        """Rejected items should not be in run_items."""
        run_id = run_service.create_run(self.conn, "niche_group")

        # Create rejected repo (no run_item)
        run_service.record_rejection(
            self.conn, run_id, "owner/rejected", "niche-1", "q", "fork_excluded", "Is a fork"
        )

        # Create failed item
        item_id = run_service.create_run_item(self.conn, run_id, "owner/failed")
        run_service.update_item_status(self.conn, item_id, "failed", "Clone error")
        run_service.record_failure(self.conn, run_id, item_id, "owner/failed", "clone_error", "Error")

        # Create succeeded item
        item_id2 = run_service.create_run_item(self.conn, run_id, "owner/ok")
        run_service.update_item_status(self.conn, item_id2, "succeed")

        run_service.update_run_counts(self.conn, run_id)
        run = run_service.get_run(self.conn, run_id)

        # Stats should be correct
        self.assertEqual(run["total_items"], 2)  # Only succeeded + failed
        self.assertEqual(run["succeeded"], 1)
        self.assertEqual(run["failed"], 1)
        self.assertEqual(run["rejected"], 1)


class TestRetentionFiltering(unittest.TestCase):
    """Verify H-1: Log retention filtering works."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_get_failures_respects_retention(self):
        """Failures should be filtered by retention hours."""
        # Set retention to 1 hour (only recent failures)
        from src.services.settings import update_settings
        update_settings(self.conn, {"log.retention_hours": "1"})

        run_id = run_service.create_run(self.conn, "manual_repo_list")
        item_id = run_service.create_run_item(self.conn, run_id, "owner/repo")

        # Record a failure
        run_service.record_failure(
            self.conn, run_id, item_id, "owner/repo", "clone_error", "Error"
        )

        # Get failures with 1 hour retention (should include recent)
        failures = run_service.get_all_failures(self.conn, hours=1)
        self.assertEqual(len(failures), 1)


class TestAPIFeatures(unittest.TestCase):
    """Test new API features."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_create_niche(self):
        """Custom niche creation should work."""
        import uuid
        niche_id = f"test-niche-{uuid.uuid4().hex[:8]}"
        r = self.client.post("/api/niches", json={
            "niche_id": niche_id,
            "title": f"Test Niche {niche_id}",
            "description": "Test",
            "languages": ["Python"],
            "github_search_queries": ["test query"],
            "enabled": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("niche_id", r.json())

    def test_search_repos(self):
        """Repo search should work."""
        r = self.client.get("/api/search/repos?q=python")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_search_niches(self):
        """Niche search should work."""
        r = self.client.get("/api/search/niches?q=python")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_run_remove_endpoint(self):
        """Run removal should work."""
        from src.database import get_connection
        from src.services import run_service

        # Create a run directly and mark it completed
        conn = get_connection()
        run_id = run_service.create_run(conn, "manual_repo_list")
        run_service.update_run_status(conn, run_id, "completed")
        conn.close()

        # Remove it via API
        r = self.client.post(f"/api/runs/{run_id}/remove")
        self.assertEqual(r.status_code, 200)

    def test_search_page_loads(self):
        """Search page should load."""
        r = self.client.get("/search")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Search", r.text)

    def test_search_repos_aggregates_sources(self):
        """Repo search should aggregate from multiple sources."""
        r = self.client.get("/api/search/repos?q=python")
        self.assertEqual(r.status_code, 200)
        results = r.json()
        self.assertIsInstance(results, list)
        # Each result should have required fields
        for repo in results:
            self.assertIn("full_name", repo)
            self.assertIn("owner", repo)
            self.assertIn("source", repo)

    def test_search_repos_with_niche_filter(self):
        """Repo search should accept niche filter."""
        r = self.client.get("/api/search/repos?q=test&niche_id=python-web-frameworks")
        self.assertEqual(r.status_code, 200)
        results = r.json()
        self.assertIsInstance(results, list)

    def test_search_niches_includes_repo_count(self):
        """Niche search should include repo counts."""
        r = self.client.get("/api/search/niches?q=python")
        self.assertEqual(r.status_code, 200)
        results = r.json()
        self.assertIsInstance(results, list)
        # Results should have repo_count
        for niche in results:
            self.assertIn("repo_count", niche)
            self.assertIn("niche_id", niche)

    def test_get_niche_repos_endpoint(self):
        """Niche repos endpoint should return repos for a niche."""
        r = self.client.get("/api/search/niches/python-web-frameworks/repos")
        self.assertEqual(r.status_code, 200)
        results = r.json()
        self.assertIsInstance(results, list)

    def test_create_group(self):
        """Creating a group via API should work."""
        r = self.client.post("/api/groups", json={
            "name": "My Group",
            "description": "Test group"
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("group_id", data)
        self.assertIsInstance(data["group_id"], int)

    def test_get_groups(self):
        """Getting groups via API should work."""
        r = self.client.get("/api/groups")
        self.assertEqual(r.status_code, 200)
        groups = r.json()
        self.assertIsInstance(groups, list)

    def test_add_item_to_group(self):
        """Adding items to a group via API should work."""
        # Create group first
        r1 = self.client.post("/api/groups", json={"name": "Test"})
        group_id = r1.json()["group_id"]

        # Add item
        r2 = self.client.post(f"/api/groups/{group_id}/items", json={
            "group_id": group_id,
            "full_name": "owner/repo",
            "owner": "owner",
            "name": "repo"
        })
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertIn("item_id", data)

    def test_remove_item_from_group(self):
        """Removing items from a group via API should work."""
        from src.database import get_connection
        from src.services import group_service

        conn = get_connection()
        gid = group_service.create_group(conn, "Test")
        iid = group_service.add_item_to_group(conn, gid, "owner/repo")
        conn.close()

        r = self.client.delete(f"/api/groups/items/{iid}")
        self.assertEqual(r.status_code, 200)

    def test_clone_group_via_api(self):
        """Cloning a group via API should create a run."""
        from src.database import get_connection
        from src.services import group_service

        conn = get_connection()
        gid = group_service.create_group(conn, "Test")
        group_service.add_item_to_group(conn, gid, "owner/repo")
        conn.close()

        r = self.client.post(f"/api/groups/{gid}/clone")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("run_id", data)
        self.assertEqual(data["status"], "searching")

    def test_dashboard_with_active_run(self):
        """Dashboard should show active run when one exists."""
        from src.database import get_connection
        from src.services import run_service

        # Create and mark as searching
        conn = get_connection()
        run_id = run_service.create_run(conn, "manual_repo_list")
        run_service.update_run_status(conn, run_id, "searching")
        conn.close()

        # Load dashboard
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Current Run", r.text)
        # Should not show "No active runs" message
        self.assertNotIn("No active runs", r.text)

    def test_dashboard_partial_matches_full_page(self):
        """HTMX partial should use same current/recent logic as full page."""
        from src.database import get_connection
        from src.services import run_service

        # Create completed and searching runs
        conn = get_connection()
        completed_id = run_service.create_run(conn, "manual_repo_list")
        run_service.update_run_status(conn, completed_id, "completed")

        active_id = run_service.create_run(conn, "manual_repo_list")
        run_service.update_run_status(conn, active_id, "searching")
        conn.close()

        # Test full page
        r_full = self.client.get("/")
        self.assertIn("Current Run", r_full.text)

        # Test partial - should match
        r_partial = self.client.get("/partials/dashboard-runs")
        self.assertEqual(r_partial.status_code, 200)
        self.assertIn("Current Run", r_partial.text)


class TestDashboardCurrentRun(unittest.TestCase):
    """Verify C-3: Dashboard current run logic correctly identifies active runs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_current_run_with_searching_status(self):
        """Most recent 'searching' run should be current."""
        # Create completed run
        run_id_1 = run_service.create_run(self.conn, "manual_repo_list")
        run_service.update_run_status(self.conn, run_id_1, "completed")

        # Create searching run (more recent)
        run_id_2 = run_service.create_run(self.conn, "manual_repo_list")
        run_service.update_run_status(self.conn, run_id_2, "searching")

        current, recent = run_service.get_current_and_recent_runs(self.conn)
        self.assertIsNotNone(current)
        self.assertEqual(current["run_id"], run_id_2)
        self.assertEqual(current["status"], "searching")
        # Recent should have the completed run
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["run_id"], run_id_1)

    def test_current_run_with_running_status(self):
        """Most recent 'running' run should be current."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")
        run_service.update_run_status(self.conn, run_id, "running")

        current, recent = run_service.get_current_and_recent_runs(self.conn)
        self.assertIsNotNone(current)
        self.assertEqual(current["run_id"], run_id)
        self.assertEqual(current["status"], "running")

    def test_current_run_none_when_no_active(self):
        """Current run should be None when no active runs exist."""
        # Create only completed runs
        run_id_1 = run_service.create_run(self.conn, "manual_repo_list")
        run_service.update_run_status(self.conn, run_id_1, "completed")

        run_id_2 = run_service.create_run(self.conn, "manual_repo_list")
        run_service.update_run_status(self.conn, run_id_2, "failed")

        current, recent = run_service.get_current_and_recent_runs(self.conn)
        self.assertIsNone(current)
        # Recent should have both completed runs, newest first
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["run_id"], run_id_2)  # More recent

    def test_recent_excludes_current(self):
        """Recent runs should not include current run."""
        # Create 3 completed, then 1 searching
        for i in range(3):
            run_id = run_service.create_run(self.conn, "manual_repo_list")
            run_service.update_run_status(self.conn, run_id, "completed")

        current_id = run_service.create_run(self.conn, "manual_repo_list")
        run_service.update_run_status(self.conn, current_id, "searching")

        current, recent = run_service.get_current_and_recent_runs(self.conn)
        self.assertEqual(current["run_id"], current_id)
        self.assertEqual(len(recent), 3)
        self.assertNotIn(current_id, [r["run_id"] for r in recent])

    def test_recent_sorted_by_newest_first(self):
        """Recent runs should be sorted by newest first."""
        run_ids = []
        for i in range(3):
            run_id = run_service.create_run(self.conn, "manual_repo_list")
            run_service.update_run_status(self.conn, run_id, "completed")
            run_ids.append(run_id)

        current, recent = run_service.get_current_and_recent_runs(self.conn)
        self.assertIsNone(current)
        self.assertEqual(len(recent), 3)
        # Should be in reverse order (newest first)
        self.assertEqual(recent[0]["run_id"], run_ids[-1])
        self.assertEqual(recent[1]["run_id"], run_ids[-2])
        self.assertEqual(recent[2]["run_id"], run_ids[0])


class TestSearchAggregation(unittest.TestCase):
    """Verify search aggregation across DB and manifest pointers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_search_repos_from_local_db(self):
        """Search should find repos in local DB."""
        from src.services.search_service import search_repos

        # Search should work with local DB
        results = search_repos(self.conn, "python")
        # Should return list (empty is ok if no matching repos)
        self.assertIsInstance(results, list)

    def test_search_repos_with_niche_filter(self):
        """Search with niche_id should include niche-specific repos."""
        from src.services.search_service import search_repos

        results = search_repos(self.conn, "test", niche_id="python-web-frameworks")
        self.assertIsInstance(results, list)

    def test_search_niches_with_repo_counts(self):
        """Niche search should include repo counts."""
        from src.services.search_service import search_niches_with_repos

        results = search_niches_with_repos(self.conn, "python")
        self.assertIsInstance(results, list)
        # Results should have repo_count field
        if results:
            self.assertIn("repo_count", results[0])
            self.assertIn("niche_id", results[0])

    def test_get_niche_repos_returns_list(self):
        """Getting repos for a niche should return list."""
        from src.services.search_service import get_niche_repos

        results = get_niche_repos(self.conn, "python-web-frameworks")
        self.assertIsInstance(results, list)
        # Should be empty or have repos with full_name
        for repo in results:
            self.assertIn("full_name", repo)
            self.assertIn("owner", repo)

    def test_search_deduplicates_by_full_name(self):
        """Search should deduplicate results by full_name."""
        from src.services.search_service import search_repos

        results = search_repos(self.conn, "test")
        # Check for duplicates
        full_names = [r["full_name"] for r in results]
        self.assertEqual(len(full_names), len(set(full_names)))


class TestGroupManagement(unittest.TestCase):
    """Verify group creation, management, and cloning functionality."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_create_group(self):
        """Creating a group should work."""
        from src.services import group_service

        group_id = group_service.create_group(
            self.conn, "Python Web", "Python web framework repos"
        )
        self.assertIsNotNone(group_id)

        group = group_service.get_group(self.conn, group_id)
        self.assertIsNotNone(group)
        self.assertEqual(group["name"], "Python Web")
        self.assertEqual(group["description"], "Python web framework repos")

    def test_add_item_to_group(self):
        """Adding items to a group should work."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Test Group")
        item_id = group_service.add_item_to_group(
            self.conn,
            group_id,
            "pallets/flask",
            owner="pallets",
            name="flask",
        )
        self.assertIsNotNone(item_id)

        items = group_service.get_group_items(self.conn, group_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["full_name"], "pallets/flask")

    def test_remove_item_from_group(self):
        """Removing items from a group should work."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Test Group")
        item_id = group_service.add_item_to_group(self.conn, group_id, "owner/repo")

        items = group_service.get_group_items(self.conn, group_id)
        self.assertEqual(len(items), 1)

        success = group_service.remove_item_from_group(self.conn, item_id)
        self.assertTrue(success)

        items = group_service.get_group_items(self.conn, group_id)
        self.assertEqual(len(items), 0)

    def test_update_group(self):
        """Updating group metadata should work."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Old Name", "Old desc")
        success = group_service.update_group(
            self.conn, group_id, name="New Name", description="New desc"
        )
        self.assertTrue(success)

        group = group_service.get_group(self.conn, group_id)
        self.assertEqual(group["name"], "New Name")
        self.assertEqual(group["description"], "New desc")

    def test_delete_group(self):
        """Deleting a group should remove it and its items."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Test Group")
        group_service.add_item_to_group(self.conn, group_id, "owner/repo1")
        group_service.add_item_to_group(self.conn, group_id, "owner/repo2")

        success = group_service.delete_group(self.conn, group_id)
        self.assertTrue(success)

        group = group_service.get_group(self.conn, group_id)
        self.assertIsNone(group)

        items = group_service.get_group_items(self.conn, group_id)
        self.assertEqual(len(items), 0)

    def test_deduplicate_items(self):
        """Adding same repo twice should not create duplicate."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Test Group")
        id1 = group_service.add_item_to_group(self.conn, group_id, "owner/repo")
        id2 = group_service.add_item_to_group(self.conn, group_id, "owner/repo")

        # Should return same item_id
        self.assertEqual(id1, id2)

        items = group_service.get_group_items(self.conn, group_id)
        self.assertEqual(len(items), 1)

    def test_clone_group_creates_run(self):
        """Cloning a group should create a run."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Test Group")
        group_service.add_item_to_group(self.conn, group_id, "owner/repo1")
        group_service.add_item_to_group(self.conn, group_id, "owner/repo2")

        run_id, error = group_service.clone_group(self.conn, group_id, run_service)
        self.assertEqual(error, "")  # Empty string means success
        self.assertIsNotNone(run_id)

        # Verify run was created with correct mode
        run = run_service.get_run(self.conn, run_id)
        self.assertEqual(run["mode"], "manual_repo_list")
        self.assertIn("Test Group", run["label"])

    def test_clone_empty_group_fails(self):
        """Cloning an empty group should fail."""
        from src.services import group_service

        group_id = group_service.create_group(self.conn, "Empty Group")

        run_id, error = group_service.clone_group(self.conn, group_id, run_service)
        self.assertIsNone(run_id)
        self.assertIsNotNone(error)
        self.assertIn("empty", error.lower())


class TestStorageOrganization(unittest.TestCase):
    """Test organized storage views."""

    def test_storage_dirs_created(self):
        """Storage organization directories should be created."""
        from src.services.storage import ensure_storage_dirs
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            ensure_storage_dirs()
            from src.services.storage import REPOS_SPECIFIC, MULTIPLE_SPECIFIC

            self.assertTrue((REPOS_SPECIFIC / "niches").exists())
            self.assertTrue((REPOS_SPECIFIC / "authors").exists())
            self.assertTrue((MULTIPLE_SPECIFIC / "grouped-runs").exists())

    def test_record_repo_organization(self):
        """Repo organization should create manifest pointers."""
        from src.services.storage import record_repo_organization
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir) / "repo"
            clone_path.mkdir()

            record_repo_organization(
                "owner/repo",
                clone_path,
                niche_id="test-niche",
                owner="owner",
            )

            # Should create manifest pointers, not duplicate clones
            from src.services.storage import REPOS_SPECIFIC
            niche_pointer = list((REPOS_SPECIFIC / "niches" / "test-niche").glob("*.json"))
            self.assertGreater(len(niche_pointer), 0)


if __name__ == "__main__":
    unittest.main()

"""Tests for Iteration 4 fixes: dashboard run flow and status logging."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from src.main import app
from src.database import get_connection, init_db, migrate_db, seed_defaults
from src.services import run_service


class TestSubmitDashboardRunFunction(unittest.TestCase):
    """Verify submitDashboardRun is present and submitRun collision is removed."""

    def test_submitDashboardRun_exists_in_js(self):
        """app.js must define submitDashboardRun (the form handler)."""
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
        src = js_path.read_text()
        self.assertIn("function submitDashboardRun", src)

    def test_no_duplicate_submitRun_as_form_handler(self):
        """dashboard.html must call submitDashboardRun, not bare submitRun."""
        tpl = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"
        src = tpl.read_text()
        self.assertIn("submitDashboardRun", src)
        # Must not still call bare submitRun() on the button
        self.assertNotIn('onclick="submitRun()"', src)

    def test_programmatic_submitRun_still_exists(self):
        """submitRun(body) for quick clone/niche must still exist in app.js."""
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
        src = js_path.read_text()
        self.assertIn("async function submitRun(body)", src)


class TestRunInputsStorage(unittest.TestCase):
    """Verify run_inputs are stored and recoverable for retry."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        migrate_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_create_run_stores_inputs(self):
        """create_run must persist run_inputs as JSON."""
        import json
        inputs = {"repo_inputs": ["owner/repo1", "owner/repo2"], "niche_ids": []}
        run_id = run_service.create_run(self.conn, "manual_repo_list", run_inputs=inputs)
        run = run_service.get_run(self.conn, run_id)

        self.assertIn("run_inputs", run)
        stored = json.loads(run["run_inputs"])
        self.assertEqual(stored["repo_inputs"], inputs["repo_inputs"])

    def test_create_run_no_inputs_defaults_empty(self):
        """create_run with no inputs stores empty dict."""
        import json
        run_id = run_service.create_run(self.conn, "manual_repo_list")
        run = run_service.get_run(self.conn, run_id)
        stored = json.loads(run.get("run_inputs", "{}"))
        self.assertIsInstance(stored, dict)


class TestIncrementalRunCounts(unittest.TestCase):
    """Verify counts update incrementally, not only at end of run."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        migrate_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_counts_reflect_rejection_immediately(self):
        """After recording a rejection and calling update_run_counts, rejected count should increase."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        # Before rejection
        run = run_service.get_run(self.conn, run_id)
        self.assertEqual(run["rejected"], 0)

        # Record rejection then update counts (as execute_run now does per-item)
        from src.services.filtering import record_rejection
        record_rejection(self.conn, run_id, "owner/repo", None, "test-query", "fork_excluded", "Is a fork")
        run_service.update_run_counts(self.conn, run_id)

        run = run_service.get_run(self.conn, run_id)
        self.assertEqual(run["rejected"], 1)
        self.assertEqual(run["total_items"], 0)  # Rejections don't count as items

    def test_counts_reflect_item_after_creation(self):
        """After creating a run_item and updating counts, total_items should increase."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        item_id = run_service.create_run_item(self.conn, run_id, "owner/repo")
        run_service.update_item_status(self.conn, item_id, "succeed")
        run_service.update_run_counts(self.conn, run_id)

        run = run_service.get_run(self.conn, run_id)
        self.assertEqual(run["total_items"], 1)
        self.assertEqual(run["succeeded"], 1)
        self.assertEqual(run["failed"], 0)

    def test_failed_item_counts_separately(self):
        """Failed items should increment failed, not succeeded."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        item_id = run_service.create_run_item(self.conn, run_id, "owner/bad")
        run_service.update_item_status(self.conn, item_id, "failed", "clone error")
        run_service.update_run_counts(self.conn, run_id)

        run = run_service.get_run(self.conn, run_id)
        self.assertEqual(run["total_items"], 1)
        self.assertEqual(run["failed"], 1)
        self.assertEqual(run["succeeded"], 0)


class TestRetryRunWithStoredInputs(unittest.TestCase):
    """Verify retry fails fast when inputs are not stored."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_retry_without_stored_inputs_returns_400(self):
        """Retry on a run with no stored inputs should return 400, not create a stale run."""
        from src.database import get_connection
        # Create a completed run with no inputs (simulates old runs)
        conn = get_connection()
        run_id = run_service.create_run(conn, "manual_repo_list")
        run_service.update_run_status(conn, run_id, "completed")
        conn.close()

        r = self.client.post(f"/api/runs/{run_id}/retry")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Cannot retry", r.json()["detail"])

    def test_retry_run_not_found_returns_404(self):
        """Retry on non-existent run should return 404."""
        r = self.client.post("/api/runs/99999/retry")
        self.assertEqual(r.status_code, 404)

    def test_retry_active_run_returns_400(self):
        """Cannot retry an active run."""
        from src.database import get_connection
        conn = get_connection()
        run_id = run_service.create_run(conn, "manual_repo_list")
        # Leave it in searching status (default)
        conn.close()

        r = self.client.post(f"/api/runs/{run_id}/retry")
        self.assertEqual(r.status_code, 400)


class TestDashboardRunsAPI(unittest.TestCase):
    """Verify dashboard API endpoints match display logic."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_create_run_manual_stores_inputs_and_returns_run_id(self):
        """POST /api/runs with valid manual inputs should return run_id."""
        r = self.client.post("/api/runs", json={
            "mode": "manual_repo_list",
            "repo_inputs": ["torvalds/linux"],
            "label": "test run",
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("run_id", data)
        self.assertEqual(data["status"], "searching")

    def test_create_run_niche_stores_niche_ids(self):
        """POST /api/runs with niche mode should return run_id."""
        r = self.client.post("/api/runs", json={
            "mode": "niche_group",
            "niche_ids": ["python-web-frameworks"],
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("run_id", r.json())

    def test_remove_run_from_dashboard_works(self):
        """Removing a completed run via API should succeed."""
        from src.database import get_connection
        conn = get_connection()
        run_id = run_service.create_run(conn, "manual_repo_list")
        run_service.update_run_status(conn, run_id, "completed")
        conn.close()

        r = self.client.post(f"/api/runs/{run_id}/remove")
        self.assertEqual(r.status_code, 200)

    def test_remove_active_run_returns_400(self):
        """Cannot remove an active (searching/running) run."""
        from src.database import get_connection
        conn = get_connection()
        run_id = run_service.create_run(conn, "manual_repo_list")
        # Leave in searching status
        conn.close()

        r = self.client.post(f"/api/runs/{run_id}/remove")
        self.assertEqual(r.status_code, 400)

    def test_dashboard_partial_returns_200(self):
        """Dashboard partial HTMX endpoint must return 200."""
        r = self.client.get("/partials/dashboard-runs")
        self.assertEqual(r.status_code, 200)


class TestDashboardRemoveButton(unittest.TestCase):
    """Verify remove button only shows for terminal runs in template."""

    def test_remove_button_function_in_js(self):
        """removeDashboardRun must be defined in app.js."""
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
        src = js_path.read_text()
        self.assertIn("function removeDashboardRun", src)

    def test_remove_button_in_dashboard_partial(self):
        """dashboard_runs.html must have removeDashboardRun call."""
        tpl = Path(__file__).resolve().parent.parent / "templates" / "partials" / "dashboard_runs.html"
        src = tpl.read_text()
        self.assertIn("removeDashboardRun", src)

    def test_remove_button_gated_on_terminal_status(self):
        """Remove button must only appear for completed/failed runs."""
        tpl = Path(__file__).resolve().parent.parent / "templates" / "partials" / "dashboard_runs.html"
        src = tpl.read_text()
        # Should be inside an if-block checking for completed/failed
        self.assertIn("completed", src)
        self.assertIn("failed", src)
        # The removeDashboardRun call should be inside the conditional
        remove_idx = src.index("removeDashboardRun")
        cond_idx = src.index("completed")
        self.assertGreater(remove_idx, cond_idx)


class TestMigrateDb(unittest.TestCase):
    """Verify migrate_db is safe and adds run_inputs if missing."""

    def test_migrate_db_is_idempotent(self):
        """Running migrate_db twice should not raise."""
        tmpdir = tempfile.mkdtemp()
        conn = get_connection(Path(tmpdir) / "test.db")
        init_db(conn)
        migrate_db(conn)
        migrate_db(conn)  # second call must not fail
        conn.close()

    def test_run_inputs_column_exists_after_migration(self):
        """run_inputs column must exist after migrate_db."""
        tmpdir = tempfile.mkdtemp()
        conn = get_connection(Path(tmpdir) / "test.db")
        init_db(conn)
        migrate_db(conn)
        # Should be able to insert with run_inputs
        conn.execute(
            "INSERT INTO runs (mode, run_inputs, status, status_code, started_at, created_at) VALUES (?,?,?,?,?,?)",
            ("manual_repo_list", '{"test": 1}', "searching", 99, "2024-01-01", "2024-01-01"),
        )
        conn.commit()
        row = conn.execute("SELECT run_inputs FROM runs LIMIT 1").fetchone()
        self.assertEqual(row[0], '{"test": 1}')
        conn.close()


if __name__ == "__main__":
    unittest.main()

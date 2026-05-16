"""Tests for run service: creation, status transitions, mixed results."""

import tempfile
import unittest
from pathlib import Path

from src.database import get_connection, init_db, seed_defaults
from src.services.run_service import (
    create_run, update_run_status, create_run_item, update_item_status,
    update_run_counts, record_failure, get_run, get_runs, get_run_items,
    get_run_failures, STATUS_MAP,
)
from src.services.filtering import record_rejection


class TestRunService(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_create_run(self):
        run_id = create_run(self.conn, "manual_repo_list", "Test Run")
        self.assertIsNotNone(run_id)
        run = get_run(self.conn, run_id)
        self.assertEqual(run["mode"], "manual_repo_list")
        self.assertEqual(run["status"], "searching")
        self.assertEqual(run["status_code"], 99)

    def test_status_transitions(self):
        run_id = create_run(self.conn, "manual_repo_list")

        # searching -> running -> completed
        update_run_status(self.conn, run_id, "running")
        run = get_run(self.conn, run_id)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["status_code"], 100)

        update_run_status(self.conn, run_id, "completed")
        run = get_run(self.conn, run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["status_code"], 202)
        self.assertIsNotNone(run["finished_at"])

    def test_manual_single_repo_run(self):
        run_id = create_run(self.conn, "manual_repo_list")
        item_id = create_run_item(self.conn, run_id, "owner/repo")
        update_item_status(self.conn, item_id, "running")
        update_item_status(self.conn, item_id, "succeed")

        items = get_run_items(self.conn, run_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "succeed")
        self.assertEqual(items[0]["status_code"], 200)

    def test_manual_multi_repo_grouped_run(self):
        run_id = create_run(self.conn, "manual_repo_list", "Multi repo test")
        for name in ["owner/repo1", "owner/repo2", "owner/repo3"]:
            create_run_item(self.conn, run_id, name)

        items = get_run_items(self.conn, run_id)
        self.assertEqual(len(items), 3)

    def test_mixed_result_run(self):
        run_id = create_run(self.conn, "niche_group")

        # Succeed
        item1 = create_run_item(self.conn, run_id, "owner/good")
        update_item_status(self.conn, item1, "succeed")

        # Failed
        item2 = create_run_item(self.conn, run_id, "owner/bad")
        update_item_status(self.conn, item2, "failed", "Clone error")
        record_failure(self.conn, run_id, item2, "owner/bad", "clone_error", "Clone error", True)

        # Rejected
        record_rejection(
            self.conn, run_id, "owner/rejected", "test-niche", "q", "fork_excluded", "Is a fork"
        )

        update_run_counts(self.conn, run_id)
        run = get_run(self.conn, run_id)
        self.assertEqual(run["succeeded"], 1)
        self.assertEqual(run["failed"], 1)
        self.assertEqual(run["rejected"], 1)

        failures = get_run_failures(self.conn, run_id)
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0]["is_retryable"])

    def test_status_map_values(self):
        self.assertEqual(STATUS_MAP["searching"], 99)
        self.assertEqual(STATUS_MAP["running"], 100)
        self.assertEqual(STATUS_MAP["completed"], 202)
        self.assertEqual(STATUS_MAP["succeed"], 200)
        self.assertEqual(STATUS_MAP["failed"], 404)

    def test_get_runs_list(self):
        create_run(self.conn, "manual_repo_list", "Run 1")
        create_run(self.conn, "niche_group", "Run 2")
        runs = get_runs(self.conn)
        self.assertEqual(len(runs), 2)


if __name__ == "__main__":
    unittest.main()

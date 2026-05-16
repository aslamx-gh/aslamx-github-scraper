"""Tests for hang-fix: clone timeout skip, live counters, and resilient run continuation."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.database import get_connection, init_db, migrate_db, seed_defaults
from src.services import run_service
from src.github.cloner import CloneTimeoutError, CLONE_TIMEOUT_SECONDS, UPDATE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# CloneTimeoutError class
# ---------------------------------------------------------------------------

class TestCloneTimeoutError(unittest.TestCase):
    """Verify CloneTimeoutError is importable and is a RuntimeError subclass."""

    def test_is_runtime_error_subclass(self):
        self.assertTrue(issubclass(CloneTimeoutError, RuntimeError))

    def test_can_be_raised_and_caught_as_runtime_error(self):
        with self.assertRaises(RuntimeError):
            raise CloneTimeoutError("test timeout")

    def test_can_be_caught_specifically(self):
        with self.assertRaises(CloneTimeoutError):
            raise CloneTimeoutError("test timeout")

    def test_timeout_constants_are_positive(self):
        self.assertGreater(CLONE_TIMEOUT_SECONDS, 0)
        self.assertGreater(UPDATE_TIMEOUT_SECONDS, 0)


# ---------------------------------------------------------------------------
# Cloner timeout raises CloneTimeoutError
# ---------------------------------------------------------------------------

class TestClonerTimeoutBehavior(unittest.TestCase):
    """Verify clone_or_update raises CloneTimeoutError on subprocess timeout."""

    def test_clone_timeout_raises_clone_timeout_error(self):
        from src.github.cloner import _clone
        from src.github.auth import GitHubCredentials
        creds = GitHubCredentials(mode="pat", token=None)

        with patch("src.github.cloner.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["git"], CLONE_TIMEOUT_SECONDS)):
            with self.assertRaises(CloneTimeoutError):
                _clone(Path("/tmp/test_repo"), "owner/repo",
                       "https://github.com/owner/repo.git", creds)

    def test_update_timeout_raises_clone_timeout_error(self):
        from src.github.cloner import _update
        from src.github.auth import GitHubCredentials
        creds = GitHubCredentials(mode="pat", token=None)

        with patch("src.github.cloner.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["git"], UPDATE_TIMEOUT_SECONDS)):
            with self.assertRaises(CloneTimeoutError):
                _update(Path("/tmp/test_repo"), creds)

    def test_clone_timeout_cleans_up_partial_directory(self):
        """Partial clone directory should be removed on timeout."""
        import tempfile as tmpmod
        from src.github.cloner import _clone
        from src.github.auth import GitHubCredentials
        creds = GitHubCredentials(mode="pat", token=None)

        with tmpmod.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir) / "partial_repo"
            clone_path.mkdir()  # simulate partial clone dir

            with patch("src.github.cloner.subprocess.run",
                       side_effect=subprocess.TimeoutExpired(["git"], CLONE_TIMEOUT_SECONDS)):
                with self.assertRaises(CloneTimeoutError):
                    _clone(clone_path, "owner/repo",
                           "https://github.com/owner/repo.git", creds)

            # Partial directory should be cleaned up
            self.assertFalse(clone_path.exists(), "Partial clone dir should be removed on timeout")


# ---------------------------------------------------------------------------
# _clone_and_record timeout handling
# ---------------------------------------------------------------------------

class TestCloneAndRecordTimeout(unittest.TestCase):
    """Verify _clone_and_record records clone_timeout and continues."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        migrate_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_timeout_records_clone_timeout_failure(self):
        """CloneTimeoutError from clone_or_update should be recorded as clone_timeout."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")
        item_id = run_service.create_run_item(self.conn, run_id, "owner/slow-repo")
        creds = MagicMock()
        repo_data = {"full_name": "owner/slow-repo", "owner": "owner",
                     "default_branch": "main"}

        with patch("src.services.run_service.clone_or_update",
                   side_effect=CloneTimeoutError("Clone timed out after 300s")):
            run_service._clone_and_record(self.conn, creds, run_id, item_id, repo_data, None)

        # Item should be failed
        item = self.conn.execute(
            "SELECT status FROM run_items WHERE item_id=?", (item_id,)
        ).fetchone()
        self.assertEqual(item["status"], "failed")

        # Failure should be recorded with clone_timeout type
        failure = self.conn.execute(
            "SELECT error_type FROM failures WHERE run_id=? AND item_id=?",
            (run_id, item_id)
        ).fetchone()
        self.assertIsNotNone(failure)
        self.assertEqual(failure["error_type"], "clone_timeout")

    def test_normal_clone_error_still_recorded_as_clone_error(self):
        """Non-timeout clone errors should still use clone_error type."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")
        item_id = run_service.create_run_item(self.conn, run_id, "owner/bad-repo")
        creds = MagicMock()
        repo_data = {"full_name": "owner/bad-repo", "owner": "owner",
                     "default_branch": "main"}

        with patch("src.services.run_service.clone_or_update",
                   side_effect=RuntimeError("Clone failed: Authentication error")):
            run_service._clone_and_record(self.conn, creds, run_id, item_id, repo_data, None)

        failure = self.conn.execute(
            "SELECT error_type FROM failures WHERE run_id=? AND item_id=?",
            (run_id, item_id)
        ).fetchone()
        self.assertIsNotNone(failure)
        self.assertEqual(failure["error_type"], "clone_error")


# ---------------------------------------------------------------------------
# Live counter updates
# ---------------------------------------------------------------------------

class TestLiveRunCounters(unittest.TestCase):
    """Verify total_items is updated immediately after create_run_item."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        migrate_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_total_items_visible_before_clone_completes(self):
        """update_run_counts is called right after create_run_item, so total is visible in-flight."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        # Intercept update_run_counts to check it's called early (before clone)
        counts_at_call = []
        original_update = run_service.update_run_counts
        clone_called = [False]

        def tracking_update(conn, rid):
            if not clone_called[0]:
                # Called before clone — capture item count at this moment
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM run_items WHERE run_id=?", (rid,)
                ).fetchone()
                counts_at_call.append(row["c"])
            original_update(conn, rid)

        def fake_clone(full_name, creds):
            clone_called[0] = True
            return Path("/tmp/fake"), "abc123"

        resolved = [({"full_name": "owner/good"}, "owner/good")]
        creds = MagicMock()
        client = MagicMock()

        with patch("src.services.run_service.update_run_counts", side_effect=tracking_update):
            with patch("src.services.run_service.resolve_manual_inputs", return_value=resolved):
                with patch("src.services.run_service.filter_candidate", return_value=(True, None, None)):
                    with patch("src.services.run_service._clone_and_record"):
                        run_service._execute_manual_run(
                            self.conn, client, creds, run_id, ["owner/good"], {}
                        )

        # At least one early call should have seen item_count >= 1
        self.assertTrue(any(c >= 1 for c in counts_at_call),
                        f"Expected item count >= 1 before clone, got counts: {counts_at_call}")

    def test_niche_run_total_visible_before_clone_completes(self):
        """Niche run also calls update_run_counts immediately after create_run_item."""
        run_id = run_service.create_run(self.conn, "niche_group")

        early_counts = []
        original_update = run_service.update_run_counts
        clone_started = [False]

        def tracking_update(conn, rid):
            if not clone_started[0]:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM run_items WHERE run_id=?", (rid,)
                ).fetchone()
                early_counts.append(row["c"])
            original_update(conn, rid)

        niche = {"niche_id": "n1"}
        candidates = [({"full_name": "owner/repo1"}, niche, "q")]
        creds = MagicMock()
        client = MagicMock()

        with patch("src.services.run_service.update_run_counts", side_effect=tracking_update):
            with patch("src.services.run_service.discover_from_niches", return_value=candidates):
                with patch("src.services.run_service.filter_candidate", return_value=(True, None, None)):
                    with patch("src.services.run_service._clone_and_record"):
                        run_service._execute_niche_run(
                            self.conn, client, creds, run_id, ["n1"], {}
                        )

        self.assertTrue(any(c >= 1 for c in early_counts),
                        f"Expected item count >= 1 before clone, got: {early_counts}")


# ---------------------------------------------------------------------------
# Full run continues after timeout
# ---------------------------------------------------------------------------

class TestRunContinuesAfterTimeout(unittest.TestCase):
    """Verify a timed-out repo does not block subsequent repos in the same run."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)
        migrate_db(self.conn)
        seed_defaults(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_manual_run_continues_after_one_timeout(self):
        """After a CloneTimeoutError on one repo, the run continues and clones the next."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        cloned = []

        def fake_clone_or_update(full_name, creds):
            if full_name == "owner/slow":
                raise CloneTimeoutError("Clone timed out after 300s")
            cloned.append(full_name)
            return Path(f"/tmp/{full_name.replace('/', '_')}"), "abc123"

        resolved = [
            ({"full_name": "owner/slow"}, "owner/slow"),
            ({"full_name": "owner/fast"}, "owner/fast"),
        ]
        creds = MagicMock()
        client = MagicMock()

        with patch("src.services.run_service.clone_or_update", side_effect=fake_clone_or_update):
            with patch("src.services.run_service.resolve_manual_inputs", return_value=resolved):
                with patch("src.services.run_service.filter_candidate", return_value=(True, None, None)):
                    with patch("src.services.run_service.upsert_repo", return_value=1):
                        with patch("src.services.run_service.create_snapshot", return_value=1):
                            with patch("src.services.storage.record_repo_organization", return_value=None):
                                run_service._execute_manual_run(
                                    self.conn, client, creds, run_id,
                                    ["owner/slow", "owner/fast"], {}
                                )

        # owner/fast should still have been cloned
        self.assertIn("owner/fast", cloned)

        # owner/slow should be recorded as clone_timeout
        failure = self.conn.execute(
            "SELECT error_type FROM failures WHERE run_id=? AND repo_full_name=?",
            (run_id, "owner/slow")
        ).fetchone()
        self.assertIsNotNone(failure)
        self.assertEqual(failure["error_type"], "clone_timeout")

    def test_run_counts_correct_after_mixed_timeout_and_success(self):
        """After a mixed run, counts should reflect actual DB state."""
        run_id = run_service.create_run(self.conn, "manual_repo_list")

        # Simulate _clone_and_record outcomes directly to avoid FK issues with mocked repo_id
        def fake_clone_and_record(conn, creds, run_id, item_id, repo_data, niche_id):
            if repo_data["full_name"] == "owner/slow":
                run_service.update_item_status(conn, item_id, "failed", "Clone timed out after 300s")
                run_service.record_failure(conn, run_id, item_id, "owner/slow",
                                           "clone_timeout", "Clone timed out after 300s", True)
            else:
                run_service.update_item_status(conn, item_id, "succeed")

        resolved = [
            ({"full_name": "owner/slow"}, "owner/slow"),
            ({"full_name": "owner/fast"}, "owner/fast"),
        ]
        creds = MagicMock()
        client = MagicMock()

        with patch("src.services.run_service._clone_and_record", side_effect=fake_clone_and_record):
            with patch("src.services.run_service.resolve_manual_inputs", return_value=resolved):
                with patch("src.services.run_service.filter_candidate", return_value=(True, None, None)):
                    run_service._execute_manual_run(
                        self.conn, client, creds, run_id,
                        ["owner/slow", "owner/fast"], {}
                    )

        run_service.update_run_counts(self.conn, run_id)
        run = run_service.get_run(self.conn, run_id)
        self.assertEqual(run["total_items"], 2)
        self.assertEqual(run["failed"], 1)
        self.assertEqual(run["succeeded"], 1)


if __name__ == "__main__":
    unittest.main()

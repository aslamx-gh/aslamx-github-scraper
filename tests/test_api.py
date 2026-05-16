"""Tests for HTTP API endpoints."""

import time
import unittest
from fastapi.testclient import TestClient

from src.main import app


class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_dashboard_loads(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dashboard", r.text)

    def test_api_runs_list(self):
        r = self.client.get("/api/runs")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_api_settings(self):
        r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("filter.require_license", data)

    def test_api_niches(self):
        r = self.client.get("/api/niches")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)
        self.assertGreaterEqual(len(r.json()), 1)

    def test_api_github_status(self):
        r = self.client.get("/api/github/status")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("auth_mode", data)
        self.assertIn("rate_limit", data)

    def test_create_run_invalid_mode(self):
        r = self.client.post("/api/runs", json={"mode": "invalid"})
        self.assertEqual(r.status_code, 400)

    def test_create_run_missing_inputs(self):
        r = self.client.post("/api/runs", json={"mode": "manual_repo_list"})
        self.assertEqual(r.status_code, 400)

    def test_api_schedules_list(self):
        r = self.client.get("/api/schedules")
        self.assertEqual(r.status_code, 200)

    def test_create_schedule_invalid_cron(self):
        r = self.client.post("/api/schedules", json={
            "name": "test",
            "cron_expression": "not valid",
            "niche_ids": [],
        })
        self.assertEqual(r.status_code, 400)

    def test_create_schedule_valid(self):
        r = self.client.post("/api/schedules", json={
            "name": "test schedule",
            "cron_expression": "0 2 * * *",
            "niche_ids": ["python-web-frameworks"],
            "enabled": False,
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("schedule_id", r.json())

    def test_ui_pages_load(self):
        for path in ["/failures", "/settings", "/niches", "/schedules", "/github"]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, f"Failed for {path}")

    def test_run_not_found(self):
        r = self.client.get("/api/runs/99999")
        self.assertEqual(r.status_code, 404)

    def test_settings_update(self):
        r = self.client.put("/api/settings", json={
            "settings": {"filter.min_stars": "10"}
        })
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/api/settings")
        self.assertEqual(r.json()["filter.min_stars"], "10")

    def test_htmx_served_locally(self):
        """HTMX must be available from static, not from an external CDN."""
        r = self.client.get("/static/js/htmx.min.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("htmx", r.text)

    def test_base_html_no_external_cdn(self):
        """base.html must not reference unpkg or other external script CDNs."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("unpkg.com", r.text)
        self.assertNotIn("cdn.jsdelivr.net", r.text)


class TestPerfSmoke(unittest.TestCase):
    """Smoke checks: server-side routes must respond within a tight wall-clock budget.

    These are not micro-benchmarks — they catch obvious regressions like blocking
    network calls or accidental DB table scans added to page-load routes.
    The TestClient is synchronous, so async routes run in a real event loop via
    anyio under the hood. The /api/github/status timeout is generous (10s) because
    it makes a live network call; all other routes must be fast (< 1s).
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _assert_fast(self, path: str, max_seconds: float = 1.0) -> None:
        t0 = time.perf_counter()
        r = self.client.get(path)
        elapsed = time.perf_counter() - t0
        self.assertEqual(r.status_code, 200, f"{path} returned {r.status_code}")
        self.assertLess(
            elapsed, max_seconds,
            f"{path} took {elapsed:.3f}s, expected < {max_seconds}s",
        )

    def test_dashboard_is_fast(self):
        self._assert_fast("/", max_seconds=1.0)

    def test_api_runs_is_fast(self):
        self._assert_fast("/api/runs", max_seconds=1.0)

    def test_api_github_status_completes(self):
        """Must complete (not hang). Generous budget covers live network round-trip."""
        self._assert_fast("/api/github/status", max_seconds=10.0)


if __name__ == "__main__":
    unittest.main()

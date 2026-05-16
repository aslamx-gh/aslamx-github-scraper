"""Tests for repo filtering logic."""

import unittest
from src.services.filtering import filter_candidate


class TestFilterCandidate(unittest.TestCase):
    def _repo(self, **overrides):
        base = {
            "full_name": "owner/repo",
            "license": "MIT",
            "is_fork": False,
            "is_archived": False,
            "size_kb": 1000,
            "languages": ["Python"],
            "stars": 100,
            "last_pushed_at": "2026-01-01T00:00:00Z",
        }
        base.update(overrides)
        return base

    def _settings(self, **overrides):
        base = {
            "require_license": True,
            "exclude_forks": True,
            "exclude_archived": True,
            "max_repo_size_kb": 512000,
            "min_stars": 0,
            "min_recent_activity_days": 0,
        }
        base.update(overrides)
        return base

    def test_accept_valid_repo(self):
        ok, reason, _ = filter_candidate(self._repo(), None, self._settings())
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_reject_fork(self):
        ok, reason, _ = filter_candidate(self._repo(is_fork=True), None, self._settings())
        self.assertFalse(ok)
        self.assertEqual(reason, "fork_excluded")

    def test_accept_fork_when_allowed(self):
        ok, reason, _ = filter_candidate(
            self._repo(is_fork=True), None, self._settings(exclude_forks=False)
        )
        self.assertTrue(ok)

    def test_reject_archived(self):
        ok, reason, _ = filter_candidate(self._repo(is_archived=True), None, self._settings())
        self.assertFalse(ok)
        self.assertEqual(reason, "archived_excluded")

    def test_reject_no_license(self):
        ok, reason, _ = filter_candidate(self._repo(license=None), None, self._settings())
        self.assertFalse(ok)
        self.assertEqual(reason, "license_missing")

    def test_accept_no_license_when_not_required(self):
        ok, reason, _ = filter_candidate(
            self._repo(license=None), None, self._settings(require_license=False)
        )
        self.assertTrue(ok)

    def test_reject_too_large(self):
        ok, reason, _ = filter_candidate(
            self._repo(size_kb=999999), None, self._settings(max_repo_size_kb=100000)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "repo_too_large")

    def test_reject_language_mismatch(self):
        niche = {"languages": ["Rust"], "exclude_forks": True, "min_stars": 0,
                 "max_repo_size_kb": 512000, "min_recent_activity_days": 0, "allowed_licenses": []}
        ok, reason, _ = filter_candidate(self._repo(languages=["Python"]), niche, self._settings())
        self.assertFalse(ok)
        self.assertEqual(reason, "language_mismatch")

    def test_accept_language_match(self):
        niche = {"languages": ["Python"], "exclude_forks": True, "min_stars": 0,
                 "max_repo_size_kb": 512000, "min_recent_activity_days": 0, "allowed_licenses": []}
        ok, reason, _ = filter_candidate(self._repo(languages=["Python"]), niche, self._settings())
        self.assertTrue(ok)

    def test_reject_low_stars(self):
        ok, reason, _ = filter_candidate(
            self._repo(stars=5), None, self._settings(min_stars=50)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "below_min_stars")

    def test_reject_activity_threshold(self):
        ok, reason, _ = filter_candidate(
            self._repo(last_pushed_at="2020-01-01T00:00:00Z"),
            None,
            self._settings(min_recent_activity_days=30),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "activity_below_threshold")



if __name__ == "__main__":
    unittest.main()

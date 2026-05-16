"""Tests for GitHub auth: PAT absent, valid, and invalid behavior."""

import os
import unittest
from unittest.mock import patch

from src.github.auth import GitHubCredentials, load_credentials, redact_token


class TestGitHubCredentials(unittest.TestCase):
    def test_anonymous_mode(self):
        cred = GitHubCredentials(mode="anonymous")
        self.assertEqual(cred.auth_header(), {})
        url = cred.clone_url_with_auth("https://github.com/owner/repo.git")
        self.assertEqual(url, "https://github.com/owner/repo.git")

    def test_pat_mode_header(self):
        cred = GitHubCredentials(mode="pat", token="ghp_test123456789012345678901234567890")
        headers = cred.auth_header()
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("token "))

    def test_pat_mode_clone_url(self):
        cred = GitHubCredentials(mode="pat", token="ghp_test123456789012345678901234567890")
        url = cred.clone_url_with_auth("https://github.com/owner/repo.git")
        self.assertIn("x-access-token:", url)
        self.assertIn("@github.com/", url)
        self.assertNotIn("https://github.com/owner", url.split("@")[0])

    def test_frozen(self):
        cred = GitHubCredentials(mode="anonymous")
        with self.assertRaises(AttributeError):
            cred.mode = "pat"


class TestLoadCredentials(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_no_token(self):
        os.environ.pop("GITHUB_TOKEN", None)
        cred = load_credentials()
        self.assertEqual(cred.mode, "anonymous")
        self.assertIsNone(cred.token)

    @patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_abcdef1234567890abcdef1234567890ab"})
    def test_with_token(self):
        cred = load_credentials()
        self.assertEqual(cred.mode, "pat")
        self.assertEqual(cred.token, "ghp_abcdef1234567890abcdef1234567890ab")

    @patch.dict(os.environ, {"GITHUB_TOKEN": "  "})
    def test_empty_token(self):
        cred = load_credentials()
        self.assertEqual(cred.mode, "anonymous")


class TestRedaction(unittest.TestCase):
    def test_redact_ghp_token(self):
        msg = "Error: auth failed with ghp_abcdef1234567890abcdef1234567890ab in request"
        result = redact_token(msg)
        self.assertNotIn("ghp_", result)
        self.assertIn("***REDACTED***", result)

    def test_no_token_unchanged(self):
        msg = "Normal error message"
        self.assertEqual(redact_token(msg), msg)


if __name__ == "__main__":
    unittest.main()

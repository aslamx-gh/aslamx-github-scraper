"""Tests for discovery: input parsing."""

import unittest
from src.services.discovery import _parse_repo_input


class TestParseRepoInput(unittest.TestCase):
    def test_owner_repo_format(self):
        self.assertEqual(_parse_repo_input("owner/repo"), "owner/repo")

    def test_full_url(self):
        self.assertEqual(
            _parse_repo_input("https://github.com/owner/repo"),
            "owner/repo",
        )

    def test_url_with_git_suffix(self):
        self.assertEqual(
            _parse_repo_input("https://github.com/owner/repo.git"),
            "owner/repo",
        )

    def test_url_with_trailing_slash(self):
        self.assertEqual(
            _parse_repo_input("https://github.com/owner/repo/"),
            "owner/repo",
        )

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_repo_input("  owner/repo  "), "owner/repo")

    def test_empty_string(self):
        self.assertIsNone(_parse_repo_input(""))

    def test_invalid_input(self):
        self.assertIsNone(_parse_repo_input("just-a-name"))


if __name__ == "__main__":
    unittest.main()

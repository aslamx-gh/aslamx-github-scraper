"""Tests for database init, config loading, and settings."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.database import get_connection, init_db, seed_defaults, upsert_niches, serialize_row
from src.config_loader import load_all_niches


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.conn = get_connection(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_tables_created(self):
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        for expected in ["niches", "repos", "repo_snapshots", "runs", "run_items",
                         "failures", "repo_rejections", "settings", "schedules"]:
            self.assertIn(expected, names)

    def test_seed_defaults(self):
        seed_defaults(self.conn)
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key='filter.require_license'"
        ).fetchone()
        self.assertEqual(row["value"], "true")

    def test_seed_idempotent(self):
        seed_defaults(self.conn)
        seed_defaults(self.conn)
        count = self.conn.execute("SELECT COUNT(*) as c FROM settings").fetchone()["c"]
        self.assertGreater(count, 0)

    def test_upsert_niches(self):
        niches = [{
            "niche_id": "test-niche",
            "title": "Test",
            "description": "A test niche",
            "languages": ["Python"],
            "github_search_queries": ["test query"],
        }]
        upsert_niches(self.conn, niches)
        row = self.conn.execute("SELECT * FROM niches WHERE niche_id='test-niche'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Test")

        # Update
        niches[0]["title"] = "Updated Test"
        upsert_niches(self.conn, niches)
        row = self.conn.execute("SELECT * FROM niches WHERE niche_id='test-niche'").fetchone()
        self.assertEqual(row["title"], "Updated Test")

    def test_serialize_row_json_arrays(self):
        self.conn.execute(
            "INSERT INTO niches (niche_id, title, languages, github_search_queries, created_at, updated_at) "
            "VALUES ('x', 'X', '[\"Python\"]', '[\"q\"]', '2024-01-01', '2024-01-01')"
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM niches WHERE niche_id='x'").fetchone()
        d = serialize_row(row)
        self.assertIsInstance(d["languages"], list)
        self.assertEqual(d["languages"], ["Python"])


class TestConfigLoader(unittest.TestCase):
    def test_load_seed_niches(self):
        niches = load_all_niches()
        self.assertGreaterEqual(len(niches), 3)
        for n in niches:
            self.assertIn("niche_id", n)
            self.assertIn("languages", n)
            self.assertIsInstance(n["languages"], list)


if __name__ == "__main__":
    unittest.main()

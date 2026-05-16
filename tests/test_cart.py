"""Tests for cart/shortlist service and API endpoints."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app
from src.database import init_db, migrate_db, seed_defaults
from src.services.cart_service import (
    add_to_cart,
    clear_cart,
    get_cart,
    get_cart_count,
    remove_from_cart,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    migrate_db(conn)
    seed_defaults(conn)
    return conn


def _sample_repo(full_name: str = "owner/repo", stars: int = 100, license: str = "MIT") -> dict:
    return {
        "full_name": full_name,
        "source_url": f"https://github.com/{full_name}",
        "owner": full_name.split("/")[0],
        "name": full_name.split("/")[1],
        "languages": ["Python"],
        "stars": stars,
        "size_kb": 500,
        "license": license,
        "topics": ["machine-learning", "tutorial"],
        "description": "A sample repo for teaching",
        "last_pushed_at": "2025-01-01T00:00:00Z",
        "quality_score": 0.75,
        "is_fork": False,
        "is_archived": False,
    }


# ---------------------------------------------------------------------------
# cart_service unit tests
# ---------------------------------------------------------------------------

class TestCartService(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = _make_conn(Path(self.tmp.name))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_empty_cart_returns_empty_list(self):
        self.assertEqual(get_cart(self.conn), [])

    def test_get_cart_count_empty(self):
        self.assertEqual(get_cart_count(self.conn), 0)

    def test_add_to_cart_returns_item_id(self):
        item_id, existed = add_to_cart(self.conn, _sample_repo())
        self.assertIsInstance(item_id, int)
        self.assertFalse(existed)

    def test_add_to_cart_item_persisted(self):
        add_to_cart(self.conn, _sample_repo("owner/myrepo"))
        items = get_cart(self.conn)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["full_name"], "owner/myrepo")

    def test_add_duplicate_returns_existing_id(self):
        id1, _ = add_to_cart(self.conn, _sample_repo())
        id2, existed = add_to_cart(self.conn, _sample_repo())
        self.assertEqual(id1, id2)
        self.assertTrue(existed)
        self.assertEqual(get_cart_count(self.conn), 1)

    def test_metadata_stored(self):
        add_to_cart(self.conn, _sample_repo("owner/repo", stars=999, license="Apache-2.0"))
        item = get_cart(self.conn)[0]
        self.assertEqual(item["stars"], 999)
        self.assertEqual(item["license"], "Apache-2.0")
        self.assertEqual(item["quality_score"], 0.75)

    def test_language_list_normalised(self):
        repo = _sample_repo()
        repo["languages"] = ["Rust", "C"]
        add_to_cart(self.conn, repo)
        item = get_cart(self.conn)[0]
        self.assertEqual(item["language"], "Rust")

    def test_remove_from_cart(self):
        item_id, _ = add_to_cart(self.conn, _sample_repo())
        self.assertTrue(remove_from_cart(self.conn, item_id))
        self.assertEqual(get_cart_count(self.conn), 0)

    def test_remove_nonexistent_returns_false(self):
        self.assertFalse(remove_from_cart(self.conn, 99999))

    def test_clear_cart(self):
        add_to_cart(self.conn, _sample_repo("a/b"))
        add_to_cart(self.conn, _sample_repo("c/d"))
        count = clear_cart(self.conn)
        self.assertEqual(count, 2)
        self.assertEqual(get_cart_count(self.conn), 0)

    def test_clear_empty_cart_returns_zero(self):
        self.assertEqual(clear_cart(self.conn), 0)

    def test_add_requires_full_name(self):
        with self.assertRaises(ValueError):
            add_to_cart(self.conn, {"stars": 100})

    def test_multiple_repos_stored_independently(self):
        for i in range(5):
            add_to_cart(self.conn, _sample_repo(f"owner/repo{i}"))
        self.assertEqual(get_cart_count(self.conn), 5)

    def test_quality_score_from_underscore_key(self):
        repo = _sample_repo()
        del repo["quality_score"]
        repo["_quality_score"] = 0.88
        add_to_cart(self.conn, repo)
        item = get_cart(self.conn)[0]
        self.assertAlmostEqual(item["quality_score"], 0.88)


# ---------------------------------------------------------------------------
# Cart API endpoint tests (uses FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestCartAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        # Clear the cart before each test so tests are independent.
        self.client.delete("/api/cart")

    def test_get_cart_empty(self):
        r = self.client.get("/api/cart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_get_cart_count(self):
        r = self.client.get("/api/cart/count")
        self.assertEqual(r.status_code, 200)
        self.assertIn("count", r.json())

    def test_add_to_cart(self):
        r = self.client.post("/api/cart", json={
            "full_name": "test/repo-api",
            "stars": 50,
            "license": "MIT",
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("item_id", data)
        self.assertFalse(data["already_in_cart"])

    def test_add_duplicate_flagged(self):
        body = {"full_name": "test/dup-repo"}
        self.client.post("/api/cart", json=body)
        r = self.client.post("/api/cart", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["already_in_cart"])

    def test_remove_from_cart(self):
        add_r = self.client.post("/api/cart", json={"full_name": "test/to-remove"})
        item_id = add_r.json()["item_id"]
        del_r = self.client.delete(f"/api/cart/{item_id}")
        self.assertEqual(del_r.status_code, 200)

    def test_remove_nonexistent_returns_404(self):
        r = self.client.delete("/api/cart/99999")
        self.assertEqual(r.status_code, 404)

    def test_clear_cart(self):
        self.client.post("/api/cart", json={"full_name": "test/clr1"})
        self.client.post("/api/cart", json={"full_name": "test/clr2"})
        r = self.client.delete("/api/cart")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["cleared"], 2)

    def test_clone_from_empty_cart_returns_400(self):
        r = self.client.post("/api/cart/clone")
        self.assertEqual(r.status_code, 400)

    def test_clone_from_cart_creates_run(self):
        self.client.post("/api/cart", json={"full_name": "test/clone-me"})
        r = self.client.post("/api/cart/clone")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("run_id", data)
        self.assertEqual(data["repo_count"], 1)

    def test_cart_ui_page_loads(self):
        r = self.client.get("/cart")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cart", r.text)

    def test_cart_in_nav(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('/cart', r.text)


if __name__ == "__main__":
    unittest.main()

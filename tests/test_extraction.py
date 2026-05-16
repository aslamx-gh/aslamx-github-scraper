"""Tests for file inspection and chunk extraction."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.services.extraction import (
    FileRecord,
    Chunk,
    inspect_repo_files,
    extract_chunks,
    _extract_python_chunks,
    _extract_js_ts_chunks,
    _extract_rust_chunks,
    _extract_doc_chunks,
    _extract_fallback_chunks,
    EXCLUDED_DIRS,
    INCLUDED_EXTENSIONS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temporary fake repo with the given files."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# File inspection
# ---------------------------------------------------------------------------

class TestInspectRepoFiles:
    def test_includes_python_source(self, tmp_path):
        _make_repo(tmp_path, {"src/main.py": "def hello():\n    pass\n"})
        records = list(inspect_repo_files(tmp_path))
        assert len(records) == 1
        r = records[0]
        assert r.included is True
        assert r.language == "python"
        assert r.relative_path == "src/main.py"

    def test_excludes_git_dir(self, tmp_path):
        _make_repo(tmp_path, {
            "src/main.py": "def hello(): pass",
            ".git/config": "[core]",
        })
        records = list(inspect_repo_files(tmp_path))
        included = [r for r in records if r.included]
        assert len(included) == 1
        assert included[0].relative_path == "src/main.py"

    def test_excludes_node_modules(self, tmp_path):
        _make_repo(tmp_path, {
            "index.js": "console.log('hi');",
            "node_modules/lodash/index.js": "// vendor",
        })
        records = list(inspect_repo_files(tmp_path))
        included = [r for r in records if r.included]
        assert len(included) == 1
        assert included[0].relative_path == "index.js"

    def test_excludes_binary_extensions(self, tmp_path):
        _make_repo(tmp_path, {
            "main.py": "x = 1",
            "image.png": b"\x89PNG".decode("latin-1"),
        })
        records = list(inspect_repo_files(tmp_path))
        py_records = [r for r in records if r.relative_path == "main.py"]
        png_records = [r for r in records if r.relative_path == "image.png"]
        assert py_records[0].included is True
        assert png_records[0].included is False
        assert png_records[0].skip_reason == "excluded_extension"

    def test_excludes_lock_files(self, tmp_path):
        _make_repo(tmp_path, {
            "package.json": '{"name": "test"}',
            "package-lock.json": "{}",
        })
        records = list(inspect_repo_files(tmp_path))
        lock = [r for r in records if "lock" in r.relative_path]
        assert lock[0].included is False
        assert lock[0].skip_reason == "excluded_filename"

    def test_excludes_oversized_file(self, tmp_path):
        big_content = "x = 1\n" * 100_000  # well over 500KB
        _make_repo(tmp_path, {"big.py": big_content})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].skip_reason == "file_too_large"
        assert records[0].included is False

    def test_includes_markdown(self, tmp_path):
        _make_repo(tmp_path, {"README.md": "# Hello\nThis is a readme."})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].included is True
        assert records[0].language == "markdown"
        assert records[0].file_kind == "doc"

    def test_includes_yaml_config(self, tmp_path):
        _make_repo(tmp_path, {"config.yml": "key: value"})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].included is True
        assert records[0].file_kind == "config"

    def test_includes_rust_source(self, tmp_path):
        _make_repo(tmp_path, {"src/lib.rs": "pub fn add(a: i32, b: i32) -> i32 { a + b }"})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].included is True
        assert records[0].language == "rust"

    def test_skips_unsupported_type(self, tmp_path):
        _make_repo(tmp_path, {"data.csv": "a,b,c\n1,2,3"})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].included is False
        assert records[0].skip_reason == "unsupported_type"

    def test_line_count_correct(self, tmp_path):
        content = "line1\nline2\nline3\n"
        _make_repo(tmp_path, {"src/f.py": content})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].line_count == 3

    def test_classifies_test_files(self, tmp_path):
        _make_repo(tmp_path, {"tests/test_foo.py": "def test_foo(): assert True"})
        records = list(inspect_repo_files(tmp_path))
        assert records[0].file_kind == "test"


# ---------------------------------------------------------------------------
# Python extraction
# ---------------------------------------------------------------------------

class TestPythonExtraction:
    def _file_record(self, path: str = "test.py") -> FileRecord:
        return FileRecord(
            relative_path=path,
            language="python",
            file_kind="source",
            size_bytes=100,
            line_count=10,
            included=True,
            skip_reason=None,
        )

    def test_extracts_function(self, tmp_path):
        code = "def greet(name):\n    return f'Hello {name}'\n"
        _make_repo(tmp_path, {"greet.py": code})
        rec = self._file_record("greet.py")
        chunks = extract_chunks(tmp_path, rec)
        assert len(chunks) >= 1
        func_chunks = [c for c in chunks if c.symbol_name == "greet"]
        assert len(func_chunks) == 1
        assert func_chunks[0].chunk_type == "function"
        assert func_chunks[0].start_line == 1
        assert "return" in func_chunks[0].content

    def test_extracts_class(self, tmp_path):
        code = "class Foo:\n    def bar(self):\n        pass\n"
        _make_repo(tmp_path, {"foo.py": code})
        rec = self._file_record("foo.py")
        chunks = extract_chunks(tmp_path, rec)
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) >= 1
        assert class_chunks[0].symbol_name == "Foo"

    def test_preserves_line_coordinates(self, tmp_path):
        code = "x = 1\n\ndef my_func():\n    return 42\n\ny = 2\n"
        _make_repo(tmp_path, {"code.py": code})
        rec = self._file_record("code.py")
        chunks = extract_chunks(tmp_path, rec)
        func = next(c for c in chunks if c.symbol_name == "my_func")
        assert func.start_line == 3

    def test_content_hash_deterministic(self, tmp_path):
        code = "def foo():\n    return 1\n"
        _make_repo(tmp_path, {"f.py": code})
        rec = self._file_record("f.py")
        c1 = extract_chunks(tmp_path, rec)
        c2 = extract_chunks(tmp_path, rec)
        assert c1[0].content_hash == c2[0].content_hash

    def test_async_function(self, tmp_path):
        code = "async def fetch(url):\n    return await client.get(url)\n"
        _make_repo(tmp_path, {"fetch.py": code})
        rec = self._file_record("fetch.py")
        chunks = extract_chunks(tmp_path, rec)
        func_chunks = [c for c in chunks if c.symbol_name == "fetch"]
        assert len(func_chunks) == 1
        assert func_chunks[0].chunk_type == "function"

    def test_empty_file_returns_empty(self, tmp_path):
        _make_repo(tmp_path, {"empty.py": "   \n  \n"})
        rec = self._file_record("empty.py")
        chunks = extract_chunks(tmp_path, rec)
        assert chunks == []

    def test_excluded_file_returns_empty(self, tmp_path):
        _make_repo(tmp_path, {"f.py": "def foo(): pass"})
        rec = FileRecord(
            relative_path="f.py", language="python", file_kind="source",
            size_bytes=20, line_count=1, included=False, skip_reason="excluded_extension"
        )
        chunks = extract_chunks(tmp_path, rec)
        assert chunks == []


# ---------------------------------------------------------------------------
# JS/TS extraction
# ---------------------------------------------------------------------------

class TestJsTsExtraction:
    def test_extracts_function_declaration(self, tmp_path):
        code = "function greet(name) {\n  return `Hello ${name}`;\n}\n"
        _make_repo(tmp_path, {"greet.js": code})
        rec = FileRecord(
            relative_path="greet.js", language="javascript", file_kind="source",
            size_bytes=100, line_count=3, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        assert any(c.symbol_name == "greet" for c in chunks)

    def test_extracts_class(self, tmp_path):
        code = "class Animal {\n  constructor(name) { this.name = name; }\n  speak() { return this.name; }\n}\n"
        _make_repo(tmp_path, {"animal.ts": code})
        rec = FileRecord(
            relative_path="animal.ts", language="typescript", file_kind="source",
            size_bytes=100, line_count=4, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) >= 1
        assert class_chunks[0].symbol_name == "Animal"

    def test_export_function(self, tmp_path):
        code = "export function hello() {\n  console.log('hi');\n}\n"
        _make_repo(tmp_path, {"hello.ts": code})
        rec = FileRecord(
            relative_path="hello.ts", language="typescript", file_kind="source",
            size_bytes=100, line_count=3, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        assert any(c.symbol_name == "hello" for c in chunks)


# ---------------------------------------------------------------------------
# Rust extraction
# ---------------------------------------------------------------------------

class TestRustExtraction:
    def test_extracts_fn(self, tmp_path):
        code = "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
        _make_repo(tmp_path, {"lib.rs": code})
        rec = FileRecord(
            relative_path="lib.rs", language="rust", file_kind="source",
            size_bytes=100, line_count=3, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        assert any(c.symbol_name == "add" for c in chunks)

    def test_extracts_struct(self, tmp_path):
        code = "pub struct Point {\n    pub x: f64,\n    pub y: f64,\n}\n"
        _make_repo(tmp_path, {"point.rs": code})
        rec = FileRecord(
            relative_path="point.rs", language="rust", file_kind="source",
            size_bytes=100, line_count=4, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        struct_chunks = [c for c in chunks if c.chunk_type == "struct"]
        assert len(struct_chunks) >= 1
        assert struct_chunks[0].symbol_name == "Point"

    def test_extracts_impl(self, tmp_path):
        code = "impl Point {\n    pub fn new(x: f64, y: f64) -> Self {\n        Point { x, y }\n    }\n}\n"
        _make_repo(tmp_path, {"point.rs": code})
        rec = FileRecord(
            relative_path="point.rs", language="rust", file_kind="source",
            size_bytes=100, line_count=5, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        impl_chunks = [c for c in chunks if c.chunk_type == "impl"]
        assert len(impl_chunks) >= 1


# ---------------------------------------------------------------------------
# Markdown extraction
# ---------------------------------------------------------------------------

class TestMarkdownExtraction:
    def test_sections_by_heading(self, tmp_path):
        content = "# Intro\nSome text.\n\n## Usage\nMore text.\n\n## API\nAPI docs.\n"
        _make_repo(tmp_path, {"README.md": content})
        rec = FileRecord(
            relative_path="README.md", language="markdown", file_kind="doc",
            size_bytes=100, line_count=8, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        titles = [c.symbol_name for c in chunks]
        assert "Intro" in titles
        assert "Usage" in titles
        assert "API" in titles

    def test_no_headings_fallback(self, tmp_path):
        content = "Just some plain text.\nNo headings here.\n"
        _make_repo(tmp_path, {"notes.md": content})
        rec = FileRecord(
            relative_path="notes.md", language="markdown", file_kind="doc",
            size_bytes=100, line_count=2, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# Fallback extraction
# ---------------------------------------------------------------------------

class TestFallbackExtraction:
    def test_fixed_sections_for_yaml(self, tmp_path):
        lines = [f"key_{i}: value_{i}" for i in range(80)]
        content = "\n".join(lines)
        _make_repo(tmp_path, {"config.yml": content})
        rec = FileRecord(
            relative_path="config.yml", language="yaml", file_kind="config",
            size_bytes=len(content), line_count=80, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        assert len(chunks) >= 2  # should split into sections

    def test_small_file_whole_chunk(self, tmp_path):
        content = "\n".join([f"item: {i}" for i in range(5)])
        _make_repo(tmp_path, {"small.yml": content})
        rec = FileRecord(
            relative_path="small.yml", language="yaml", file_kind="config",
            size_bytes=len(content), line_count=5, included=True, skip_reason=None
        )
        chunks = extract_chunks(tmp_path, rec)
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "file"


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

class TestDBPersistence:
    def _setup_db(self):
        import sqlite3
        from src.database import init_db, migrate_db, get_connection
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        init_db(conn)
        migrate_db(conn)
        return conn

    def _seed_repo_snapshot(self, conn):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (owner, name, full_name, source_url, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("owner", "repo", "owner/repo", "https://github.com/owner/repo", now, now)
        )
        repo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO repo_snapshots (repo_id, commit_sha, branch, snapshot_at, ingestion_status) VALUES (?,?,?,?,?)",
            (repo_id, "abc123", "main", now, "completed")
        )
        snapshot_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return repo_id, snapshot_id

    def test_store_file_record(self):
        from src.services.extraction import store_file_record
        conn = self._setup_db()
        repo_id, snapshot_id = self._seed_repo_snapshot(conn)
        rec = FileRecord(
            relative_path="src/main.py", language="python", file_kind="source",
            size_bytes=500, line_count=20, included=True, skip_reason=None
        )
        file_id = store_file_record(conn, snapshot_id, repo_id, rec)
        conn.commit()
        row = conn.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        assert row["relative_path"] == "src/main.py"
        assert row["language"] == "python"
        assert row["included"] == 1

    def test_store_chunk(self):
        from src.services.extraction import store_file_record, store_chunk
        conn = self._setup_db()
        repo_id, snapshot_id = self._seed_repo_snapshot(conn)
        rec = FileRecord(
            relative_path="src/main.py", language="python", file_kind="source",
            size_bytes=500, line_count=20, included=True, skip_reason=None
        )
        file_id = store_file_record(conn, snapshot_id, repo_id, rec)
        conn.commit()
        chunk = Chunk(
            file_path="src/main.py", language="python", chunk_type="function",
            symbol_name="my_func", start_line=5, end_line=10,
            content="def my_func():\n    return 42\n",
        )
        classification = {
            "longevity_band": "medium",
            "longevity_confidence": 0.65,
            "primary_tag": "utilities",
            "summary": "python function 'my_func'",
            "quality_flags": [],
        }
        chunk_id = store_chunk(conn, file_id, snapshot_id, repo_id, chunk, classification)
        conn.commit()
        row = conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        assert row["symbol_name"] == "my_func"
        assert row["start_line"] == 5
        assert row["longevity_band"] == "medium"

    def test_store_chunk_tags(self):
        from src.services.extraction import store_file_record, store_chunk, store_chunk_tags
        conn = self._setup_db()
        repo_id, snapshot_id = self._seed_repo_snapshot(conn)
        rec = FileRecord(
            relative_path="src/f.py", language="python", file_kind="source",
            size_bytes=100, line_count=5, included=True, skip_reason=None
        )
        file_id = store_file_record(conn, snapshot_id, repo_id, rec)
        conn.commit()
        chunk = Chunk(
            file_path="src/f.py", language="python", chunk_type="function",
            symbol_name="foo", start_line=1, end_line=3,
            content="def foo(): pass",
        )
        chunk_id = store_chunk(conn, file_id, snapshot_id, repo_id, chunk, {
            "longevity_band": "small", "longevity_confidence": 0.5,
            "primary_tag": None, "summary": "", "quality_flags": [],
        })
        store_chunk_tags(conn, chunk_id, ["utilities", "testing"])
        conn.commit()
        tags = conn.execute(
            "SELECT tag FROM chunk_tags WHERE chunk_id=?", (chunk_id,)
        ).fetchall()
        assert {r["tag"] for r in tags} == {"utilities", "testing"}

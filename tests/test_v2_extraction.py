"""V2 Phase 3: AST boundary and doc code-block extraction tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.extraction import (
    Chunk,
    FileRecord,
    extract_chunks,
    _extract_python_chunks,
)
from src.services.extraction_ast import extract_python_ast


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _py_record(path: str = "test.py") -> FileRecord:
    return FileRecord(
        relative_path=path,
        language="python",
        file_kind="source",
        size_bytes=100,
        line_count=10,
        included=True,
        skip_reason=None,
    )


def _md_record(path: str = "README.md") -> FileRecord:
    return FileRecord(
        relative_path=path,
        language="markdown",
        file_kind="doc",
        size_bytes=100,
        line_count=10,
        included=True,
        skip_reason=None,
    )


def _make_file(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# AST boundary tests
# ---------------------------------------------------------------------------

class TestASTBoundaries:
    def test_decorator_included_in_start_line(self, tmp_path):
        code = "@staticmethod\ndef foo():\n    return 1\n"
        _make_file(tmp_path, "f.py", code)
        chunks = extract_chunks(tmp_path, _py_record("f.py"))
        foo = next(c for c in chunks if c.symbol_name == "foo")
        assert foo.start_line == 1  # decorator line, not def line

    def test_class_and_methods_extracted_separately(self, tmp_path):
        code = (
            "class Greeter:\n"
            "    def greet(self):\n"
            "        return 'hi'\n"
            "    def bye(self):\n"
            "        return 'bye'\n"
        )
        _make_file(tmp_path, "g.py", code)
        chunks = extract_chunks(tmp_path, _py_record("g.py"))
        types = {c.chunk_type for c in chunks}
        names = {c.symbol_name for c in chunks}
        assert "class" in types
        assert "method" in types
        assert "Greeter" in names
        assert "greet" in names
        assert "bye" in names
        class_chunk = next(c for c in chunks if c.chunk_type == "class")
        assert class_chunk.end_line == 5  # class spans full body

    def test_async_function_chunk_type_is_function(self, tmp_path):
        code = "async def fetch(url):\n    return await get(url)\n"
        _make_file(tmp_path, "f.py", code)
        chunks = extract_chunks(tmp_path, _py_record("f.py"))
        fetch = next(c for c in chunks if c.symbol_name == "fetch")
        assert fetch.chunk_type == "function"

    def test_multiple_top_level_functions_each_get_chunk(self, tmp_path):
        code = "def foo():\n    pass\n\ndef bar():\n    return 1\n"
        _make_file(tmp_path, "f.py", code)
        chunks = extract_chunks(tmp_path, _py_record("f.py"))
        names = {c.symbol_name for c in chunks}
        assert "foo" in names
        assert "bar" in names

    def test_ast_accurate_end_line(self, tmp_path):
        # foo ends at line 2; bar starts at line 5 (two blank lines between)
        code = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
        _make_file(tmp_path, "f.py", code)
        chunks = extract_chunks(tmp_path, _py_record("f.py"))
        foo = next(c for c in chunks if c.symbol_name == "foo")
        bar = next(c for c in chunks if c.symbol_name == "bar")
        assert foo.end_line == 2
        assert bar.start_line == 5

    def test_syntax_error_falls_back_gracefully(self, tmp_path):
        # Broken syntax — AST fails, regex fallback runs
        code = "def broken(\n    pass\n"
        _make_file(tmp_path, "broken.py", code)
        # Must not raise; must return a list
        chunks = extract_chunks(tmp_path, _py_record("broken.py"))
        assert isinstance(chunks, list)
        assert len(chunks) >= 1  # fallback must produce something, not silently return []

    def test_class_only_no_methods(self, tmp_path):
        code = "class Empty:\n    pass\n"
        _make_file(tmp_path, "e.py", code)
        chunks = extract_chunks(tmp_path, _py_record("e.py"))
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0].symbol_name == "Empty"

    def test_multiple_decorators_start_at_first(self, tmp_path):
        code = "@app.route('/')\n@login_required\ndef index():\n    return 'ok'\n"
        _make_file(tmp_path, "views.py", code)
        chunks = extract_chunks(tmp_path, _py_record("views.py"))
        index = next(c for c in chunks if c.symbol_name == "index")
        assert index.start_line == 1  # first decorator, not def line

    def test_extract_python_chunks_direct_call(self):
        code = "def hello():\n    return 'world'\n"
        lines = code.splitlines()
        chunks = _extract_python_chunks(code, lines, "hello.py")
        assert len(chunks) == 1
        assert chunks[0].symbol_name == "hello"


# ---------------------------------------------------------------------------
# Doc code-block extraction tests
# ---------------------------------------------------------------------------

class TestDocCodeBlocks:
    def test_fenced_block_emits_code_block_chunk(self, tmp_path):
        content = "# Intro\n\n```python\nx = 1\n```\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        assert len(code_blocks) == 1
        assert "x = 1" in code_blocks[0].content

    def test_fenced_block_language_hint_in_symbol_name(self, tmp_path):
        content = "# Usage\n\n```python\nprint('hi')\n```\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        assert code_blocks[0].symbol_name == "python"

    def test_fenced_block_no_language_hint_symbol_name_is_none(self, tmp_path):
        content = "# Usage\n\n```\nsome command\n```\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        assert len(code_blocks) == 1
        assert code_blocks[0].symbol_name is None

    def test_multiple_fenced_blocks_each_emitted(self, tmp_path):
        content = "# API\n\n```python\ndef foo(): pass\n```\n\nText.\n\n```bash\necho hi\n```\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        assert len(code_blocks) == 2
        langs = {c.symbol_name for c in code_blocks}
        assert langs == {"python", "bash"}

    def test_sections_still_emitted_alongside_code_blocks(self, tmp_path):
        content = "# Intro\n\nText.\n\n```python\nx = 1\n```\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        section_chunks = [c for c in chunks if c.chunk_type == "section"]
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        assert len(section_chunks) >= 1
        assert len(code_blocks) == 1

    def test_no_fenced_blocks_sections_only_no_regression(self, tmp_path):
        content = "# Intro\nSome text.\n\n## Usage\nMore text.\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        section_chunks = [c for c in chunks if c.chunk_type == "section"]
        assert len(code_blocks) == 0
        assert len(section_chunks) >= 2

    def test_unclosed_fence_at_eof_still_emits_chunk(self, tmp_path):
        content = "# Intro\n\n```python\nx = 1\n# no closing fence\n"
        _make_file(tmp_path, "README.md", content)
        chunks = extract_chunks(tmp_path, _md_record("README.md"))
        code_blocks = [c for c in chunks if c.chunk_type == "code_block"]
        assert len(code_blocks) == 1
        assert "x = 1" in code_blocks[0].content

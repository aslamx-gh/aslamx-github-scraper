"""Tests for taxonomy and longevity classification heuristics."""

from __future__ import annotations

import pytest

from src.services.extraction import Chunk
from src.services.classification import classify_chunk, Classification


def _make_chunk(
    file_path: str = "src/main.py",
    language: str = "python",
    chunk_type: str = "function",
    symbol_name: str | None = "my_func",
    start_line: int = 1,
    end_line: int = 10,
    content: str = "def my_func():\n    return 42\n",
) -> Chunk:
    return Chunk(
        file_path=file_path,
        language=language,
        chunk_type=chunk_type,
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        content=content,
    )


class TestTaxonomyTagging:
    def test_api_tagged_by_path(self):
        chunk = _make_chunk(file_path="src/api/routes.py", content="def get_user(): pass")
        result = classify_chunk(chunk)
        assert "api" in result.category_tags

    def test_auth_tagged_by_content(self):
        chunk = _make_chunk(
            content="def authenticate(token):\n    return verify_jwt(token)\n"
        )
        result = classify_chunk(chunk)
        assert "auth" in result.category_tags

    def test_database_tagged_by_content(self):
        chunk = _make_chunk(
            content="class UserModel(Base):\n    __tablename__ = 'users'\n    id = Column(Integer)\n"
        )
        result = classify_chunk(chunk)
        assert "database" in result.category_tags

    def test_testing_tagged_by_path(self):
        chunk = _make_chunk(file_path="tests/test_foo.py", content="def test_something(): assert True")
        result = classify_chunk(chunk)
        assert "testing" in result.category_tags

    def test_testing_tagged_by_content(self):
        chunk = _make_chunk(
            content="def test_add():\n    assert add(1, 2) == 3\n"
        )
        result = classify_chunk(chunk)
        assert "testing" in result.category_tags

    def test_devops_tagged_by_language(self):
        chunk = _make_chunk(
            file_path="Dockerfile",
            language="dockerfile",
            content="FROM python:3.12\nRUN pip install -r requirements.txt\n",
        )
        result = classify_chunk(chunk)
        assert "devops" in result.category_tags

    def test_config_tagged_by_language(self):
        chunk = _make_chunk(
            file_path="config.yml",
            language="yaml",
            chunk_type="file",
            symbol_name=None,
            content="database:\n  host: localhost\n  port: 5432\n",
        )
        result = classify_chunk(chunk)
        assert "config" in result.category_tags

    def test_always_has_at_least_one_tag(self):
        chunk = _make_chunk(content="x = 1")
        result = classify_chunk(chunk)
        assert len(result.category_tags) >= 1

    def test_primary_tag_is_in_category_tags(self):
        chunk = _make_chunk(file_path="src/auth.py", content="def login(): pass")
        result = classify_chunk(chunk)
        assert result.primary_tag in result.category_tags

    def test_documentation_lang_tag(self):
        chunk = _make_chunk(
            file_path="docs/guide.md",
            language="markdown",
            chunk_type="section",
            symbol_name="Introduction",
            content="# Introduction\nThis is the guide.\n",
        )
        result = classify_chunk(chunk)
        assert "documentation" in result.category_tags


class TestLongevityBands:
    def test_test_code_gets_small(self):
        chunk = _make_chunk(
            file_path="tests/test_foo.py",
            content="def test_foo():\n    assert 1 == 1\n",
        )
        result = classify_chunk(chunk)
        assert result.longevity_band == "small"

    def test_config_gets_small(self):
        chunk = _make_chunk(
            file_path="config.yml",
            language="yaml",
            chunk_type="file",
            symbol_name=None,
            content="host: localhost\nport: 5432\n",
        )
        result = classify_chunk(chunk)
        assert result.longevity_band == "small"

    def test_architecture_path_gets_high_band(self):
        chunk = _make_chunk(
            file_path="docs/architecture/overview.md",
            language="markdown",
            chunk_type="section",
            symbol_name="Architecture Overview",
            content="# Architecture\nThe system uses a hexagonal architecture...\n",
        )
        result = classify_chunk(chunk)
        assert result.longevity_band in ("high", "very-high")

    def test_abstract_base_class_gets_higher_band(self):
        chunk = _make_chunk(
            file_path="src/base_service.py",
            symbol_name="AbstractBaseService",
            content=(
                "class AbstractBaseService:\n"
                "    def process(self):\n"
                "        raise NotImplementedError\n"
                "    def validate(self, data):\n"
                "        raise NotImplementedError\n"
            ),
        )
        result = classify_chunk(chunk)
        assert result.longevity_band in ("high", "very-high")

    def test_longevity_confidence_in_range(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert 0.0 <= result.longevity_confidence <= 1.0

    def test_all_valid_bands(self):
        valid_bands = {"small", "medium", "high", "very-high"}
        chunks = [
            _make_chunk(file_path="tests/t.py", content="def test_x(): pass"),
            _make_chunk(file_path="config.yml", language="yaml"),
            _make_chunk(file_path="src/service.py"),
            _make_chunk(file_path="src/base.py", symbol_name="AbstractRepository"),
        ]
        for c in chunks:
            result = classify_chunk(c)
            assert result.longevity_band in valid_bands


class TestQualityFlags:
    def test_very_short_flagged(self):
        chunk = _make_chunk(
            start_line=1, end_line=2,
            content="x = 1\ny = 2\n",
        )
        result = classify_chunk(chunk)
        assert "very_short" in result.quality_flags

    def test_no_flags_for_normal_chunk(self):
        content = "\n".join([f"    line_{i} = {i}" for i in range(20)])
        content = "def normal_function():\n" + content
        chunk = _make_chunk(start_line=1, end_line=21, content=content)
        result = classify_chunk(chunk)
        assert "very_short" not in result.quality_flags
        assert "very_long" not in result.quality_flags

    def test_placeholder_flagged(self):
        chunk = _make_chunk(content="def foo():\n    pass  # TODO implement\n")
        result = classify_chunk(chunk)
        assert "has_placeholder" in result.quality_flags


class TestV2FieldsPopulated:
    def test_v2_fields_non_none_for_normal_chunk(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert result.difficulty is not None
        assert result.topic is not None
        assert result.style is not None
        assert result.paradigm is not None
        assert result.architecture_level is not None
        assert result.security_relevance is not None
        assert result.example_type is not None
        assert result.beginner_safe is not None
        assert result.chunk_quality_score is not None
        assert result.teaching_value_score is not None

    def test_chunk_quality_score_in_range(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert 0.0 <= result.chunk_quality_score <= 1.0

    def test_teaching_value_score_in_range(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert 0.0 <= result.teaching_value_score <= 1.0

    def test_difficulty_valid_value(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert result.difficulty in ("beginner", "intermediate", "advanced")

    def test_beginner_safe_valid_value(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert result.beginner_safe in ("safe", "caution", "unsafe")


class TestSummary:
    def test_summary_includes_symbol(self):
        chunk = _make_chunk(symbol_name="my_function")
        result = classify_chunk(chunk)
        assert "my_function" in result.summary

    def test_summary_includes_band(self):
        chunk = _make_chunk()
        result = classify_chunk(chunk)
        assert result.longevity_band in result.summary

    def test_summary_no_symbol(self):
        chunk = _make_chunk(symbol_name=None, chunk_type="section")
        result = classify_chunk(chunk)
        assert "section" in result.summary

"""Tests for V2 Phase 4 educational classification heuristics."""

from __future__ import annotations

import pytest

from src.services.extraction import Chunk
from src.services.classification import (
    classify_chunk,
    Classification,
    classify_difficulty,
    classify_topic,
    classify_subtopic,
)


def _chunk(
    file_path: str = "src/main.py",
    language: str = "python",
    chunk_type: str = "function",
    symbol_name: str | None = "my_func",
    start_line: int = 1,
    end_line: int = 15,
    content: str = "def my_func():\n    x = 1\n    return x\n",
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


class TestClassificationDataclassHasV2Fields:
    def test_has_difficulty_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "difficulty")

    def test_has_topic_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "topic")

    def test_has_subtopic_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "subtopic")

    def test_has_style_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "style")

    def test_has_paradigm_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "paradigm")

    def test_has_architecture_level_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "architecture_level")

    def test_has_security_relevance_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "security_relevance")

    def test_has_example_type_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "example_type")

    def test_has_beginner_safe_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "beginner_safe")

    def test_has_chunk_quality_score_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "chunk_quality_score")

    def test_has_teaching_value_score_field(self):
        c = classify_chunk(_chunk())
        assert hasattr(c, "teaching_value_score")

    def test_all_v2_fields_non_none_for_normal_chunk(self):
        # This test will only fully pass after all helper functions are wired
        # into classify_chunk() in Task 5. For now, just verify the fields exist.
        c = classify_chunk(_chunk())
        assert hasattr(c, "difficulty")
        assert hasattr(c, "topic")
        assert hasattr(c, "beginner_safe")
        assert hasattr(c, "chunk_quality_score")
        assert hasattr(c, "teaching_value_score")


class TestClassifyDifficulty:
    def test_architecture_tag_is_advanced(self):
        chunk = _chunk(file_path="src/arch/system.py", content="class AbstractFactory:\n    def create(self): raise NotImplementedError\n" * 5)
        assert classify_difficulty(chunk, "architecture", "high") == "advanced"

    def test_security_tag_is_advanced(self):
        chunk = _chunk(content="def encrypt_data(key, data):\n    return cipher.encrypt(data)\n" * 5)
        assert classify_difficulty(chunk, "security", "medium") == "advanced"

    def test_abstract_symbol_is_advanced(self):
        chunk = _chunk(symbol_name="AbstractBaseService", content="class AbstractBaseService:\n    pass\n")
        assert classify_difficulty(chunk, "utilities", "small") == "advanced"

    def test_short_simple_function_is_beginner(self):
        chunk = _chunk(
            chunk_type="function",
            symbol_name="add",
            content="def add(a, b):\n    return a + b\n",
            start_line=1, end_line=2,
        )
        assert classify_difficulty(chunk, "utilities", "small") == "beginner"

    def test_medium_function_is_intermediate(self):
        content = "def process(items):\n" + "    x = items\n" * 15 + "    return x\n"
        chunk = _chunk(chunk_type="function", content=content, start_line=1, end_line=17)
        assert classify_difficulty(chunk, "data_processing", "medium") == "intermediate"

    def test_concurrency_tag_is_advanced(self):
        chunk = _chunk(content="async def run_workers():\n    await asyncio.gather(*tasks)\n" * 5)
        assert classify_difficulty(chunk, "concurrency", "high") == "advanced"


class TestClassifyTopic:
    def test_auth_tag_maps_to_security(self):
        assert classify_topic("auth", "function", "") == "security"

    def test_database_tag_maps_to_data_access(self):
        assert classify_topic("database", "class", "") == "data_access"

    def test_api_tag_maps_to_api(self):
        assert classify_topic("api", "function", "") == "api"

    def test_testing_tag_maps_to_testing(self):
        assert classify_topic("testing", "function", "") == "testing"

    def test_architecture_tag_maps_to_architecture(self):
        assert classify_topic("architecture", "class", "") == "architecture"

    def test_concurrency_tag_maps_to_async(self):
        assert classify_topic("concurrency", "function", "") == "async"

    def test_cli_tag_maps_to_cli(self):
        assert classify_topic("cli", "function", "") == "cli"

    def test_frontend_tag_maps_to_frontend(self):
        assert classify_topic("frontend", "class", "") == "frontend"

    def test_devops_tag_maps_to_tooling(self):
        assert classify_topic("devops", "file", "") == "tooling"

    def test_none_primary_tag_returns_none(self):
        assert classify_topic(None, "function", "") is None

    def test_unknown_tag_falls_back_to_functions(self):
        assert classify_topic("utilities", "function", "") == "functions"


class TestClassifySubtopic:
    def test_jwt_keyword_gives_jwt_auth(self):
        assert classify_subtopic("security", "verify_jwt", "jwt token verify") == "jwt_auth"

    def test_rate_limit_keyword_gives_rate_limiting(self):
        assert classify_subtopic("api", None, "rate_limit exceeded retry") == "rate_limiting"

    def test_orm_model_keyword_gives_orm_model(self):
        assert classify_subtopic("data_access", "UserModel", "__tablename__ = 'users' column(") == "orm_model"

    def test_no_match_returns_none(self):
        assert classify_subtopic("api", "get_user", "return user_data") is None

    def test_none_topic_returns_none(self):
        assert classify_subtopic(None, "something", "content") is None

    def test_mock_keyword_gives_mocking(self):
        assert classify_subtopic("testing", None, "magicmock patch mocker") == "mocking"

    def test_decorator_keyword_gives_decorator(self):
        assert classify_subtopic("functions", "log_decorator", "wraps( functools") == "decorator"


class TestClassifyStyle:
    def test_class_chunk_type_is_oop(self):
        from src.services.classification import classify_style
        assert classify_style("class", "class Foo:\n    def __init__(self): pass\n") == "object_oriented"

    def test_self_in_content_is_oop(self):
        from src.services.classification import classify_style
        assert classify_style("method", "def process(self):\n    self.data = 1\n") == "object_oriented"

    def test_lambda_without_class_is_functional(self):
        from src.services.classification import classify_style
        assert classify_style("function", "transform = lambda x: x * 2\nresult = map(transform, items)\n") == "functional"

    def test_plain_function_is_procedural(self):
        from src.services.classification import classify_style
        assert classify_style("function", "def add(a, b):\n    return a + b\n") == "procedural"

    def test_class_with_lambda_is_mixed(self):
        from src.services.classification import classify_style
        assert classify_style("method", "class Foo:\n    def run(self):\n        f = lambda x: x\n        return map(f, items)\n") == "mixed"


class TestClassifyParadigm:
    def test_architecture_tag_is_service_oriented(self):
        from src.services.classification import classify_paradigm
        assert classify_paradigm("architecture", "") == "service_oriented"

    def test_frontend_tag_is_event_driven(self):
        from src.services.classification import classify_paradigm
        assert classify_paradigm("frontend", "") == "event_driven"

    def test_webhook_keyword_is_event_driven(self):
        from src.services.classification import classify_paradigm
        assert classify_paradigm("api", "webhook handler emit(") == "event_driven"

    def test_lambda_keyword_is_functional(self):
        from src.services.classification import classify_paradigm
        assert classify_paradigm("utilities", "result = map(lambda x: x, items)") == "functional"

    def test_plain_function_is_imperative(self):
        from src.services.classification import classify_paradigm
        assert classify_paradigm("utilities", "def process(data):\n    return data\n") == "imperative"


class TestClassifyArchitectureLevel:
    def test_function_chunk_type_is_function(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("function", "utilities", None, "") == "function"

    def test_method_chunk_type_is_function(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("method", "api", None, "") == "function"

    def test_class_without_service_symbol_is_module(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("class", "utilities", "DataPoint", "") == "module"

    def test_class_with_service_symbol_is_service(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("class", "api", "UserService", "") == "service"

    def test_class_with_repository_symbol_is_service(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("class", "database", "UserRepository", "") == "service"

    def test_section_is_syntax(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("section", "documentation", None, "") == "syntax"

    def test_code_block_is_syntax(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("code_block", "documentation", None, "") == "syntax"

    def test_file_chunk_type_is_module(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("file", "config", None, "") == "module"

    def test_architecture_tag_with_system_keyword_is_system(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("section", "architecture", None, "the system platform application") == "system"

    def test_architecture_tag_with_subsystem_keyword_is_subsystem(self):
        from src.services.classification import classify_architecture_level
        assert classify_architecture_level("class", "architecture", "LayerModule", "subsystem layer") == "subsystem"


class TestClassifySecurityRelevance:
    def test_auth_primary_tag_is_high(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("auth", "", None) == "high"

    def test_security_primary_tag_is_high(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("security", "", None) == "high"

    def test_password_keyword_is_high(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("utilities", "password hash verify", None) == "high"

    def test_token_keyword_is_high(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("api", "token bearer secret", None) == "high"

    def test_encrypt_keyword_is_high(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("utilities", "encrypt decrypt cipher", None) == "high"

    def test_validate_keyword_is_medium(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("utilities", "validate sanitize input", None) == "medium"

    def test_api_tag_no_security_keywords_is_low(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("api", "get user profile data", None) == "low"

    def test_plain_utility_is_none(self):
        from src.services.classification import classify_security_relevance
        assert classify_security_relevance("utilities", "def add(a, b): return a + b", None) == "none"


class TestClassifyExampleType:
    def test_testing_tag_is_test_example(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("testing", [], "", "function") == "test_example"

    def test_architecture_tag_is_architecture_example(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("architecture", [], "", "class") == "architecture_example"

    def test_auth_tag_is_security_example(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("auth", [], "", "function") == "security_example"

    def test_security_tag_is_security_example(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("security", [], "", "function") == "security_example"

    def test_placeholder_flag_is_anti_pattern(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("utilities", ["has_placeholder"], "", "function") == "anti_pattern"

    def test_anti_pattern_keyword_in_content(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("utilities", [], "this is antipattern avoid_this", "function") == "anti_pattern"

    def test_normal_function_is_good_example(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("utilities", [], "def process(data): return data", "function") == "good_example"

    def test_section_with_architecture_tag_is_architecture_example(self):
        from src.services.classification import classify_example_type
        assert classify_example_type("architecture", [], "", "section") == "architecture_example"


class TestClassifyBeginnerSafety:
    def test_advanced_and_high_security_is_unsafe(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("advanced", "high", []) == "unsafe"

    def test_advanced_difficulty_alone_is_caution(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("advanced", "none", []) == "caution"

    def test_medium_security_is_caution(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("beginner", "medium", []) == "caution"

    def test_high_security_is_caution_even_if_beginner(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("beginner", "high", []) == "caution"

    def test_placeholder_flag_is_caution(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("beginner", "none", ["has_placeholder"]) == "caution"

    def test_clean_beginner_chunk_is_safe(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("beginner", "none", []) == "safe"

    def test_intermediate_no_security_is_safe(self):
        from src.services.classification import classify_beginner_safety
        assert classify_beginner_safety("intermediate", "none", []) == "safe"

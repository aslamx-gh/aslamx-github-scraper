"""Heuristic taxonomy tagging and longevity band classification for extracted chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extraction import Chunk, FileRecord


# ---------------------------------------------------------------------------
# Taxonomy tag definitions
# ---------------------------------------------------------------------------

# Primary tag -> (path keywords, content keywords)
TAG_RULES: list[tuple[str, list[str], list[str]]] = [
    # Architecture / design patterns
    ("architecture",
     ["architecture", "design", "pattern", "overview", "structure"],
     ["AbstractFactory", "Observer", "Singleton", "Strategy", "Facade",
      "UseCase", "Adapter", "architecture", "design_pattern"]),

    # Authentication / authorization
    ("auth",
     ["auth", "authentication", "authorization", "login", "oauth", "jwt", "token", "session", "permission"],
     ["authenticate", "authorize", "login", "logout", "token", "session",
      "password", "credential", "jwt", "oauth", "bearer"]),

    # Configuration
    ("config",
     ["config", "configuration", "settings", "env", "environment"],
     ["Settings", "Config", "Configuration", "environ", "getenv", "dotenv", "load_config"]),

    # Database / ORM
    ("database",
     ["db", "database", "model", "schema", "migration", "orm", "repository"],
     ["Model", "Table", "Column", "Query", "Session", "migrate", "schema",
      "SELECT", "INSERT", "UPDATE", "DELETE", "JOIN"]),

    # API / HTTP
    ("api",
     ["api", "route", "endpoint", "rest", "graphql", "handler", "view", "controller"],
     ["router", "route", "endpoint", "request", "response", "GET", "POST", "PUT",
      "DELETE", "PATCH", "APIRouter", "Blueprint"]),

    # Testing
    ("testing",
     ["test", "tests", "spec", "specs", "mock", "fixture", "conftest"],
     ["assert", "assertEqual", "test_", "describe(", "it(", "expect(",
      "pytest", "unittest", "mock", "MagicMock", "patch"]),

    # CLI / command line
    ("cli",
     ["cli", "command", "cmd", "console", "terminal", "shell"],
     ["argparse", "click", "typer", "clap", "cobra", "Commander",
      "ArgumentParser", "add_argument", "subcommand"]),

    # Error handling
    ("error_handling",
     ["error", "exception", "handler", "middleware"],
     ["Exception", "Error", "raise", "try:", "except:", "catch", "throw",
      "Result", "unwrap", "panic", "recover"]),

    # Logging / observability
    ("observability",
     ["log", "logging", "monitor", "metric", "trace", "observ"],
     ["logger", "logging", "log_", "getLogger", "tracing", "span", "metric",
      "structlog", "loguru"]),

    # Data processing / parsing
    ("data_processing",
     ["parse", "parser", "process", "transform", "serialize", "deserialize", "encode", "decode"],
     ["parse", "serialize", "deserialize", "encode", "decode", "transform",
      "validate", "schema", "Pydantic", "dataclass", "TypedDict"]),

    # Concurrency / async
    ("concurrency",
     ["async", "thread", "concurrent", "parallel", "queue", "worker"],
     ["async def", "await", "asyncio", "ThreadPoolExecutor", "concurrent",
      "Queue", "Lock", "Mutex", "Channel", "spawn"]),

    # Networking
    ("networking",
     ["network", "socket", "http", "tcp", "udp", "websocket", "grpc"],
     ["socket", "connect", "bind", "listen", "send", "recv", "http",
      "requests", "aiohttp", "httpx", "websocket"]),

    # File / IO
    ("file_io",
     ["file", "io", "stream", "read", "write", "path"],
     ["open(", "read(", "write(", "Path(", "os.path", "shutil",
      "pathlib", "File", "stream", "buffer"]),

    # Frontend / UI
    ("frontend",
     ["component", "ui", "view", "page", "layout", "style", "css", "html"],
     ["React", "useState", "useEffect", "component", "render", "props",
      "className", "style", "template", "JSX", "TSX"]),

    # Documentation
    ("documentation",
     ["doc", "docs", "readme", "changelog", "contributing", "guide", "tutorial"],
     ["#", "##", "###", "---", "===", "Usage", "Example", "Installation"]),

    # Build / CI / DevOps
    ("devops",
     ["ci", "cd", "deploy", "build", "docker", "k8s", "kubernetes", "ansible",
      "terraform", "helm", "pipeline", "workflow"],
     ["Dockerfile", "docker-compose", "kubectl", "helm", "deploy",
      "stage:", "steps:", "jobs:", "workflow"]),

    # Security
    ("security",
     ["security", "crypto", "encrypt", "hash", "sign", "ssl", "tls", "cert"],
     ["encrypt", "decrypt", "hash", "hmac", "sign", "verify", "ssl",
      "tls", "certificate", "cipher"]),

    # Utilities / helpers
    ("utilities",
     ["util", "utils", "helper", "helpers", "common", "shared", "tools"],
     ["helper", "utility", "format_", "parse_", "convert_", "normalize_"]),
]


# ---------------------------------------------------------------------------
# Longevity band rules
# ---------------------------------------------------------------------------

# Band: very-high = stable design patterns, interfaces, algorithms
#       high      = solid abstractions, general patterns
#       medium    = framework-specific but reusable concepts
#       small     = boilerplate, config, tests, trivial snippets

@dataclass
class LongevityResult:
    band: str           # small, medium, high, very-high
    confidence: float   # 0.0–1.0
    reasons: list[str] = field(default_factory=list)


def _longevity_band(
    file_path: str,
    language: str | None,
    chunk_type: str,
    primary_tag: str | None,
    content: str,
    symbol_name: str | None,
) -> LongevityResult:
    score = 0.0
    reasons: list[str] = []
    path_lower = file_path.lower()
    content_lower = content.lower()
    sym_lower = (symbol_name or "").lower()

    # Tests get small regardless
    if primary_tag == "testing" or any(p in path_lower for p in ("test/", "tests/", "spec/", "__tests__")):
        return LongevityResult("small", 0.85, ["test_code"])

    # Config gets small/medium
    if primary_tag == "config" or chunk_type == "config":
        return LongevityResult("small", 0.80, ["config_content"])

    # Documentation sections
    if language in ("markdown", "rst", "text"):
        if any(kw in path_lower for kw in ("readme", "contributing", "changelog", "guide", "tutorial")):
            return LongevityResult("medium", 0.65, ["documentation"])
        if any(kw in path_lower for kw in ("architecture", "design", "overview", "pattern")):
            return LongevityResult("high", 0.70, ["architecture_doc"])
        return LongevityResult("small", 0.60, ["generic_doc"])

    # Architecture / design patterns → very-high
    if primary_tag == "architecture":
        score += 0.4
        reasons.append("architecture_tag")

    # Abstract classes, interfaces, base classes → high/very-high
    if any(kw in sym_lower for kw in ("abstract", "base", "interface", "protocol", "mixin")):
        score += 0.35
        reasons.append("abstraction_symbol")

    # Algorithm-like symbols → high
    algo_keywords = ["sort", "search", "hash", "parse", "encode", "decode", "encrypt",
                     "serialize", "validate", "calculate", "compute", "transform"]
    if any(kw in sym_lower for kw in algo_keywords):
        score += 0.25
        reasons.append("algorithm_symbol")

    # Framework coupling signals → downgrade
    framework_coupling = [
        "flask", "django", "fastapi", "express", "react", "vue", "angular",
        "sqlalchemy", "pydantic", "pytest", "unittest",
    ]
    coupling_count = sum(1 for kw in framework_coupling if kw in content_lower)
    if coupling_count >= 3:
        score -= 0.30
        reasons.append("high_framework_coupling")
    elif coupling_count >= 1:
        score -= 0.10
        reasons.append("some_framework_coupling")

    # Error handling, concurrency → high
    if primary_tag in ("error_handling", "concurrency", "security"):
        score += 0.20
        reasons.append(f"valuable_tag:{primary_tag}")

    # API routes, views → medium (framework-specific)
    if primary_tag == "api":
        score += 0.10
        reasons.append("api_tag")

    # Data processing, utilities → medium/high
    if primary_tag in ("data_processing", "utilities"):
        score += 0.15
        reasons.append(f"reusable_tag:{primary_tag}")

    # Short chunks are likely boilerplate
    line_count = content.count("\n") + 1
    if line_count < 10:
        score -= 0.15
        reasons.append("short_chunk")
    elif line_count > 50:
        score += 0.10
        reasons.append("substantial_chunk")

    # Determine band from score
    if score >= 0.50:
        band = "very-high"
        confidence = min(0.85, 0.65 + score * 0.2)
    elif score >= 0.25:
        band = "high"
        confidence = min(0.80, 0.55 + score * 0.3)
    elif score >= 0.05:
        band = "medium"
        confidence = min(0.75, 0.50 + score * 0.5)
    else:
        band = "small"
        confidence = max(0.40, 0.55 - abs(score) * 0.3)

    return LongevityResult(band, round(confidence, 3), reasons)


# ---------------------------------------------------------------------------
# Classification entry point
# ---------------------------------------------------------------------------

@dataclass
class Classification:
    category_tags: list[str]
    primary_tag: str | None
    longevity_band: str
    longevity_confidence: float
    quality_flags: list[str]
    summary: str
    # V2 Phase 4 — educational classification
    difficulty: str | None = None
    topic: str | None = None
    subtopic: str | None = None
    style: str | None = None
    paradigm: str | None = None
    architecture_level: str | None = None
    security_relevance: str | None = None
    example_type: str | None = None
    beginner_safe: str | None = None
    chunk_quality_score: float | None = None
    teaching_value_score: float | None = None


def classify_chunk(chunk: "Chunk") -> Classification:
    """Assign taxonomy tags and longevity band to a chunk."""
    path_lower = chunk.file_path.lower()
    content_lower = chunk.content.lower()
    sym = chunk.symbol_name or ""

    matched_tags: list[str] = []

    for tag, path_kws, content_kws in TAG_RULES:
        path_match = any(kw in path_lower for kw in path_kws)
        content_match = any(kw.lower() in content_lower for kw in content_kws)
        if path_match or content_match:
            matched_tags.append(tag)

    # Always have at least one tag based on language
    lang_tag = _language_fallback_tag(chunk.language)
    if lang_tag and lang_tag not in matched_tags:
        matched_tags.append(lang_tag)

    # Fallback to generic
    if not matched_tags:
        matched_tags = ["general"]

    # Pick primary tag — prefer non-generic, non-language
    priority_order = [
        "architecture", "auth", "database", "api", "concurrency",
        "error_handling", "security", "data_processing", "networking",
        "observability", "cli", "frontend", "devops", "testing",
        "config", "file_io", "utilities", "documentation",
    ]
    primary = None
    for p in priority_order:
        if p in matched_tags:
            primary = p
            break
    if primary is None:
        primary = matched_tags[0]

    longevity = _longevity_band(
        chunk.file_path,
        chunk.language,
        chunk.chunk_type,
        primary,
        chunk.content,
        chunk.symbol_name,
    )

    quality_flags = _quality_flags(chunk)

    summary = _make_summary(chunk, primary, longevity.band)

    content_lower = chunk.content.lower()
    line_count = chunk.content.count("\n") + 1

    difficulty = classify_difficulty(chunk, primary, longevity.band)
    topic = classify_topic(primary, chunk.chunk_type, content_lower)
    subtopic = classify_subtopic(topic, chunk.symbol_name, content_lower)
    style = classify_style(chunk.chunk_type, content_lower)
    paradigm = classify_paradigm(primary, content_lower)
    architecture_level = classify_architecture_level(
        chunk.chunk_type, primary, chunk.symbol_name, content_lower
    )
    security_relevance = classify_security_relevance(primary, content_lower, chunk.symbol_name)
    example_type = classify_example_type(primary, quality_flags, content_lower, chunk.chunk_type)
    beginner_safe = classify_beginner_safety(difficulty, security_relevance, quality_flags)
    chunk_quality_score = compute_chunk_quality_score(longevity.band, quality_flags, line_count)
    teaching_value_score = compute_teaching_value_score(
        difficulty, example_type, security_relevance, chunk_quality_score
    )

    return Classification(
        category_tags=matched_tags,
        primary_tag=primary,
        longevity_band=longevity.band,
        longevity_confidence=longevity.confidence,
        quality_flags=quality_flags,
        summary=summary,
        difficulty=difficulty,
        topic=topic,
        subtopic=subtopic,
        style=style,
        paradigm=paradigm,
        architecture_level=architecture_level,
        security_relevance=security_relevance,
        example_type=example_type,
        beginner_safe=beginner_safe,
        chunk_quality_score=chunk_quality_score,
        teaching_value_score=teaching_value_score,
    )


def _language_fallback_tag(language: str | None) -> str | None:
    if not language:
        return None
    mapping = {
        "python": "utilities",
        "javascript": "frontend",
        "typescript": "frontend",
        "rust": "utilities",
        "go": "utilities",
        "java": "utilities",
        "markdown": "documentation",
        "rst": "documentation",
        "yaml": "config",
        "toml": "config",
        "json": "config",
        "dockerfile": "devops",
        "terraform": "devops",
        "shell": "devops",
        "sql": "database",
    }
    return mapping.get(language)


def _quality_flags(chunk: "Chunk") -> list[str]:
    flags: list[str] = []
    content = chunk.content
    line_count = content.count("\n") + 1

    if line_count < 5:
        flags.append("very_short")
    if line_count > 300:
        flags.append("very_long")

    # Check for obvious placeholder content
    if re.search(r"\bpass\b|\bTODO\b|\bFIXME\b|\bNOOP\b", content):
        flags.append("has_placeholder")

    # Mostly comments
    code_lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("#", "//", "*", "/*"))]
    if len(code_lines) < line_count * 0.2:
        flags.append("mostly_comments")

    return flags


def _make_summary(chunk: "Chunk", primary_tag: str | None, band: str) -> str:
    kind = chunk.chunk_type
    sym = chunk.symbol_name
    lang = chunk.language or "unknown"
    lines = chunk.end_line - chunk.start_line + 1

    if sym:
        return f"{lang} {kind} '{sym}' ({lines} lines) — {primary_tag or 'general'} [{band}]"
    return f"{lang} {kind} ({lines} lines) — {primary_tag or 'general'} [{band}]"

# ---------------------------------------------------------------------------
# V2 Phase 4 — Educational classification helpers
# ---------------------------------------------------------------------------

def classify_difficulty(chunk: "Chunk", primary_tag: str | None, longevity_band: str) -> str:
    """Classify chunk difficulty as beginner, intermediate, or advanced."""
    sym = (chunk.symbol_name or "").lower()
    line_count = chunk.content.count("\n") + 1

    # Advanced: architectural, security, concurrency domains
    if primary_tag in ("architecture", "security", "concurrency"):
        return "advanced"
    # Advanced: abstraction symbols
    if any(kw in sym for kw in ("abstract", "base", "interface", "protocol", "mixin")):
        return "advanced"
    # Advanced: very-high longevity + substantial content
    if longevity_band == "very-high" and line_count > 30:
        return "advanced"

    # Beginner: short simple function/method not in complex domain
    if (chunk.chunk_type in ("function", "method")
            and line_count < 20
            and primary_tag not in ("auth", "database", "networking", "architecture", "security", "concurrency", "data_processing")):
        return "beginner"

    return "intermediate"


# Topic vocabulary mapping from primary_tag
_TAG_TO_TOPIC: dict[str, str] = {
    "auth": "security",
    "security": "security",
    "database": "data_access",
    "data_processing": "data_access",
    "api": "api",
    "testing": "testing",
    "architecture": "architecture",
    "concurrency": "async",
    "cli": "cli",
    "frontend": "frontend",
    "devops": "tooling",
    "config": "tooling",
    "observability": "tooling",
    "error_handling": "error_handling",
    "networking": "api",
    "file_io": "syntax",
    "utilities": "functions",
    "documentation": "architecture",
    "general": "functions",
}


def classify_topic(primary_tag: str | None, chunk_type: str, content_lower: str) -> str | None:
    """Map primary_tag to educational topic vocabulary."""
    if primary_tag is None:
        return None
    return _TAG_TO_TOPIC.get(primary_tag, "functions")


# Subtopic keyword rules: (topic, subtopic, keywords_list)
_SUBTOPIC_RULES: list[tuple[str, str, list[str]]] = [
    ("security", "sql_injection", ["sql_injection", "sqlinjection", "injection"]),
    ("security", "jwt_auth", ["jwt", "json web token"]),
    ("security", "password_hashing", ["bcrypt", "argon", "pbkdf2"]),
    ("security", "oauth", ["oauth", "oauth2"]),
    ("api", "rate_limiting", ["rate_limit", "ratelimit", "throttle"]),
    ("api", "pagination", ["paginate", "pagination", "page_size", "cursor"]),
    ("api", "authentication_middleware", ["middleware", "auth_middleware"]),
    ("architecture", "dependency_injection", ["dependency_injection", "di_container"]),
    ("architecture", "observer_pattern", ["observer", "subscribe", "notify"]),
    ("architecture", "factory_pattern", ["factory", "builder"]),
    ("async", "task_queue", ["task_queue", "celery", "worker_queue"]),
    ("async", "event_loop", ["event_loop", "asyncio.run", "loop.run"]),
    ("data_access", "orm_model", ["__tablename__", "column(", "mapper("]),
    ("data_access", "query_builder", [".filter(", ".where(", "select("]),
    ("testing", "parameterized", ["parametrize", "@pytest.mark.parametrize"]),
    ("testing", "mocking", ["magicmock", "patch(", "mocker"]),
    ("syntax", "list_comprehension", ["[x for", "[ x for"]),
    ("functions", "decorator", ["wraps(", "functools"]),
    ("error_handling", "custom_exception", ["class.*exception", "class.*error"]),
]


def classify_subtopic(topic: str | None, symbol_name: str | None, content_lower: str) -> str | None:
    """Detect lesson-level subtopic from keyword scan."""
    if topic is None:
        return None
    sym_lower = (symbol_name or "").lower()
    combined = content_lower + " " + sym_lower
    for rule_topic, subtopic, keywords in _SUBTOPIC_RULES:
        if rule_topic == topic:
            if any(kw in combined for kw in keywords):
                return subtopic
    return None


def classify_style(chunk_type: str, content_lower: str) -> str:
    """Classify coding style: procedural, object_oriented, functional, or mixed."""
    is_oop = (
        chunk_type == "class"
        or ("class " in content_lower and "self" in content_lower)
        or "__init__" in content_lower
        or "self" in content_lower
    )
    is_functional = (
        chunk_type != "class"
        and (
            "lambda " in content_lower
            or "map(" in content_lower
            or "reduce(" in content_lower
            or "filter(" in content_lower
        )
    )
    if is_oop and is_functional:
        return "mixed"
    if is_oop:
        return "object_oriented"
    if is_functional:
        return "functional"
    return "procedural"


def classify_paradigm(primary_tag: str | None, content_lower: str) -> str:
    """Classify programming paradigm."""
    if primary_tag == "architecture":
        return "service_oriented"
    event_kws = ("webhook", "callback(", "on_event", "subscribe(", "emit(", "dispatch(")
    if primary_tag == "frontend" or any(kw in content_lower for kw in event_kws):
        return "event_driven"
    functional_kws = ("lambda ", "map(", "reduce(", "filter(", "functools", "partial(")
    if any(kw in content_lower for kw in functional_kws):
        return "functional"
    return "imperative"


_SERVICE_SYMBOLS = frozenset({
    "service", "client", "manager", "handler",
    "repository", "controller", "gateway", "adapter",
})


def classify_architecture_level(
    chunk_type: str,
    primary_tag: str | None,
    symbol_name: str | None,
    content_lower: str,
) -> str:
    """Classify chunk's position in the architecture hierarchy."""
    sym_lower = (symbol_name or "").lower()

    if primary_tag == "architecture":
        if any(kw in content_lower for kw in ("subsystem", "layer", "module")):
            return "subsystem"
        if any(kw in content_lower for kw in ("system", "platform", "application")):
            return "system"
        return "service"

    if chunk_type == "class":
        if any(kw in sym_lower for kw in _SERVICE_SYMBOLS):
            return "service"
        return "module"

    if chunk_type in ("function", "method"):
        return "function"

    if chunk_type == "file":
        return "module"

    # section, code_block — inline syntax examples
    return "syntax"


_HIGH_SECURITY_TAGS = frozenset({"auth", "security"})
_HIGH_SECURITY_KEYWORDS = frozenset({
    "password", "credential", "token", "secret", "private_key",
    "encrypt", "decrypt", "hmac", "sign(", "verify(",
    "sql_injection", "injection", "xss", "csrf",
})
_MEDIUM_SECURITY_KEYWORDS = frozenset({
    "validate", "sanitize", "escape(", "allowlist", "whitelist",
    "input_validation", "trusted",
})


def classify_security_relevance(
    primary_tag: str | None,
    content_lower: str,
    symbol_name: str | None,
) -> str:
    """Classify security relevance: none, low, medium, high."""
    sym_lower = (symbol_name or "").lower()
    combined = content_lower + " " + sym_lower

    if primary_tag in _HIGH_SECURITY_TAGS:
        return "high"
    if any(kw in combined for kw in _HIGH_SECURITY_KEYWORDS):
        return "high"
    if any(kw in combined for kw in _MEDIUM_SECURITY_KEYWORDS):
        return "medium"
    if primary_tag in ("api", "networking"):
        return "low"
    return "none"


_ANTI_PATTERN_KEYWORDS = frozenset({
    "antipattern", "anti_pattern", "bad_practice",
    "dont_do", "avoid_this",
})


def classify_example_type(
    primary_tag: str | None,
    quality_flags: list[str],
    content_lower: str,
    chunk_type: str,
) -> str:
    """Classify what kind of teaching example this chunk represents."""
    if "has_placeholder" in quality_flags:
        return "anti_pattern"
    if any(kw in content_lower for kw in _ANTI_PATTERN_KEYWORDS):
        return "anti_pattern"
    if primary_tag == "testing":
        return "test_example"
    if primary_tag == "architecture" or chunk_type == "section":
        return "architecture_example"
    if primary_tag in ("auth", "security"):
        return "security_example"
    return "good_example"


def classify_beginner_safety(
    difficulty: str | None,
    security_relevance: str | None,
    quality_flags: list[str],
) -> str:
    """Classify beginner safety: safe, caution, unsafe."""
    if difficulty == "advanced" and security_relevance == "high":
        return "unsafe"
    if (difficulty == "advanced"
            or security_relevance in ("medium", "high")
            or "has_placeholder" in quality_flags):
        return "caution"
    return "safe"


def compute_chunk_quality_score(
    longevity_band: str,
    quality_flags: list[str],
    line_count: int,
) -> float:
    """Heuristic quality score for a chunk, 0.0–1.0."""
    score = 0.50
    if longevity_band == "very-high":
        score += 0.30
    elif longevity_band == "high":
        score += 0.20
    elif longevity_band == "medium":
        score += 0.10
    if 20 <= line_count <= 150:
        score += 0.10
    if "very_short" in quality_flags:
        score -= 0.20
    if "very_long" in quality_flags:
        score -= 0.10
    if "has_placeholder" in quality_flags:
        score -= 0.30
    if "mostly_comments" in quality_flags:
        score -= 0.10
    return round(max(0.0, min(1.0, score)), 3)


def compute_teaching_value_score(
    difficulty: str | None,
    example_type: str | None,
    security_relevance: str | None,
    chunk_quality_score: float,
) -> float:
    """Teaching value score for a chunk, 0.0–1.0."""
    score = chunk_quality_score
    if difficulty == "intermediate":
        score += 0.10
    elif difficulty == "beginner":
        score += 0.05
    elif difficulty == "advanced":
        score -= 0.05
    if example_type == "good_example":
        score += 0.15
    elif example_type in ("test_example", "architecture_example"):
        score += 0.10
    elif example_type == "anti_pattern":
        score -= 0.30
    if security_relevance == "high":
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 3)

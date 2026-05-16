"""Deterministic teaching artifact generation for validated chunks.

Generates three artifact types from existing chunk metadata — no LLM calls:
  - explanation_seed: concise description derived from classification fields
  - prerequisite_hints: list of concepts a learner needs before studying this chunk
  - exercise_prompt: a practice task matched to difficulty and chunk type

Only generates artifacts for chunks with validation_status='accepted'.
Skips chunks that already have an artifact on disk (idempotent).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..database import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template tables
# ---------------------------------------------------------------------------

_TOPIC_PREREQS: dict[str, list[str]] = {
    "functions":      ["function definition syntax", "parameters and return values", "variable scope"],
    "testing":        ["test assertions", "test isolation", "pytest basics"],
    "api":            ["HTTP request/response cycle", "status codes", "REST conventions"],
    "async":          ["coroutines and async/await", "event loop basics", "concurrency vs parallelism"],
    "data_access":    ["SQL SELECT/INSERT basics", "database connections", "query parameterization"],
    "error_handling": ["exception types", "try/except/finally", "error propagation"],
    "security":       ["authentication vs authorization", "input validation", "least privilege"],
    "architecture":   ["module organisation", "separation of concerns", "interface contracts"],
    "frontend":       ["DOM structure", "event listeners", "component state"],
    "cli":            ["argument parsing", "stdin/stdout", "exit codes"],
    "tooling":        ["build systems", "configuration formats", "development workflow"],
    "syntax":         ["language fundamentals", "type system", "operator precedence"],
    "general":        ["programming fundamentals", "code organisation"],
}

_PARADIGM_PREREQS: dict[str, list[str]] = {
    "functional":       ["pure functions", "immutability", "higher-order functions"],
    "object_oriented":  ["classes and instances", "inheritance", "method dispatch"],
    "event_driven":     ["event emitters", "callback functions", "async patterns"],
    "service_oriented": ["service contracts", "API boundaries", "dependency injection"],
    "imperative":       ["control flow", "mutable state", "sequential execution"],
}

_EXERCISE_PROMPTS: dict[str, list[str]] = {
    "beginner": [
        "Trace through this {chunk_type} with a sample input and write down what it returns.",
        "Add a docstring to this {chunk_type} describing its purpose and parameters.",
        "Call this {chunk_type} from a simple script and print the result.",
    ],
    "intermediate": [
        "Write a unit test covering the main behaviour of this {chunk_type}.",
        "Identify at least one edge case this {chunk_type} does not handle, and add handling for it.",
        "Add input validation to this {chunk_type} and raise a meaningful error for invalid inputs.",
    ],
    "advanced": [
        "Refactor this {chunk_type} to reduce coupling and improve testability.",
        "Analyse the time and space complexity of this {chunk_type} and document your findings.",
        "Extend this {chunk_type} to support an additional use case without breaking existing callers.",
    ],
}

_DEFAULT_DIFFICULTY = "intermediate"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class TeachingArtifact:
    chunk_id: int
    repo_id: int
    snapshot_id: int
    content_hash: str
    explanation_seed: str
    prerequisite_hints: list[str]
    exercise_prompt: str
    generated_at: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "repo_id": self.repo_id,
            "snapshot_id": self.snapshot_id,
            "content_hash": self.content_hash,
            "explanation_seed": self.explanation_seed,
            "prerequisite_hints": self.prerequisite_hints,
            "exercise_prompt": self.exercise_prompt,
            "generated_at": self.generated_at,
        }


@dataclass
class TeachingArtifactReport:
    snapshot_id: int | None
    eligible_count: int
    generated: int = 0
    skipped_existing: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "eligible_count": self.eligible_count,
            "generated": self.generated,
            "skipped_existing": self.skipped_existing,
            "errors": self.errors,
        }


def generate_teaching_artifacts(
    db_path: Path | None = None,
    snapshot_id: int | None = None,
) -> TeachingArtifactReport:
    """
    Generate teaching artifacts for all accepted chunks, optionally scoped to a snapshot.

    Idempotent: skips chunks whose artifact file already exists on disk.
    Only processes validation_status='accepted' chunks.
    """
    conn = get_connection(db_path)
    report = TeachingArtifactReport(snapshot_id=snapshot_id, eligible_count=0)

    try:
        if snapshot_id is not None:
            rows = conn.execute(
                """SELECT c.chunk_id, c.repo_id, c.snapshot_id, c.content_hash,
                          c.chunk_type, c.symbol_name, c.language,
                          c.difficulty, c.topic, c.subtopic, c.style, c.paradigm,
                          c.architecture_level, c.security_relevance,
                          c.example_type, c.beginner_safe, c.teaching_value_score,
                          f.relative_path AS file_path
                   FROM chunks c
                   JOIN files f ON f.file_id = c.file_id
                   WHERE c.validation_status = 'accepted'
                     AND c.snapshot_id = ?""",
                (snapshot_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.chunk_id, c.repo_id, c.snapshot_id, c.content_hash,
                          c.chunk_type, c.symbol_name, c.language,
                          c.difficulty, c.topic, c.subtopic, c.style, c.paradigm,
                          c.architecture_level, c.security_relevance,
                          c.example_type, c.beginner_safe, c.teaching_value_score,
                          f.relative_path AS file_path
                   FROM chunks c
                   JOIN files f ON f.file_id = c.file_id
                   WHERE c.validation_status = 'accepted'""",
            ).fetchall()

        report.eligible_count = len(rows)

        from .manifests import write_teaching_sidecar, TEACHING_DIR

        for row in rows:
            chunk_id = row["chunk_id"]
            artifact_path = TEACHING_DIR / f"chunk_{chunk_id}.json"
            if artifact_path.exists():
                report.skipped_existing += 1
                continue

            try:
                artifact = _build_artifact(row)
                write_teaching_sidecar(artifact)
                report.generated += 1
            except Exception as exc:
                logger.warning("Failed to generate teaching artifact for chunk %d: %s", chunk_id, exc)
                report.errors.append(f"chunk {chunk_id}: {exc}")

    except Exception as exc:
        logger.error("Teaching artifact generation error: %s", exc)
        report.errors.append(str(exc))
    finally:
        conn.close()

    logger.info(
        "Teaching artifacts (snapshot=%s): %d eligible, %d generated, %d skipped, %d errors",
        snapshot_id, report.eligible_count, report.generated,
        report.skipped_existing, len(report.errors),
    )
    return report


# ---------------------------------------------------------------------------
# Internal generation logic
# ---------------------------------------------------------------------------

def _build_artifact(row) -> TeachingArtifact:
    from datetime import datetime, timezone

    chunk_id = row["chunk_id"]
    chunk_type = row["chunk_type"] or "chunk"
    symbol_name = row["symbol_name"]
    file_path = row["file_path"] or "unknown"
    language = row["language"] or "unknown"
    difficulty = row["difficulty"] or _DEFAULT_DIFFICULTY
    topic = row["topic"] or "general"
    subtopic = row["subtopic"]
    paradigm = row["paradigm"] or "imperative"

    explanation_seed = _make_explanation_seed(
        chunk_type, symbol_name, file_path, language,
        difficulty, topic, subtopic, paradigm,
        row["architecture_level"], row["security_relevance"],
        row["beginner_safe"],
    )
    prerequisite_hints = _make_prerequisite_hints(topic, paradigm)
    exercise_prompt = _make_exercise_prompt(difficulty, chunk_type, chunk_id)

    return TeachingArtifact(
        chunk_id=chunk_id,
        repo_id=row["repo_id"],
        snapshot_id=row["snapshot_id"],
        content_hash=row["content_hash"],
        explanation_seed=explanation_seed,
        prerequisite_hints=prerequisite_hints,
        exercise_prompt=exercise_prompt,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_explanation_seed(
    chunk_type: str,
    symbol_name: str | None,
    file_path: str,
    language: str,
    difficulty: str,
    topic: str,
    subtopic: str | None,
    paradigm: str,
    architecture_level: str | None,
    security_relevance: str | None,
    beginner_safe: str | None,
) -> str:
    symbol_label = f"`{symbol_name}`" if symbol_name else f"anonymous {chunk_type}"
    topic_label = f"{topic} / {subtopic}" if subtopic else topic
    arch = architecture_level or "function"
    lines = [
        f"{chunk_type.title()} {symbol_label} in `{file_path}`",
        f"Language: {language} | Difficulty: {difficulty} | Topic: {topic_label}",
        f"Pattern: {paradigm}, {arch}-level",
    ]
    if security_relevance and security_relevance != "none":
        lines.append(f"Security relevance: {security_relevance}")
    if beginner_safe == "safe":
        lines.append("Suitable for beginners.")
    elif beginner_safe == "caution":
        lines.append("Caution: may require additional context for beginners.")
    elif beginner_safe == "unsafe":
        lines.append("Not recommended as a first example for beginners.")
    return "\n".join(lines)


def _make_prerequisite_hints(topic: str, paradigm: str) -> list[str]:
    hints: list[str] = []
    hints.extend(_TOPIC_PREREQS.get(topic, _TOPIC_PREREQS["general"]))
    paradigm_hints = _PARADIGM_PREREQS.get(paradigm, [])
    for h in paradigm_hints:
        if h not in hints:
            hints.append(h)
    return hints


def _make_exercise_prompt(difficulty: str, chunk_type: str, chunk_id: int) -> str:
    prompts = _EXERCISE_PROMPTS.get(difficulty, _EXERCISE_PROMPTS[_DEFAULT_DIFFICULTY])
    template = prompts[chunk_id % len(prompts)]
    return template.format(chunk_type=chunk_type)

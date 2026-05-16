"""Run tracking, ingestion orchestration, and failure recording."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from ..github.auth import GitHubCredentials, load_credentials, redact_token
from ..github.client import GitHubClient
from ..github.cloner import clone_or_update, CloneTimeoutError
from ..database import get_connection, serialize_row, serialize_rows
from .discovery import discover_from_niches, resolve_manual_inputs
from .filtering import filter_candidate, get_filter_settings, record_rejection
from .extraction import (
    inspect_repo_files, extract_chunks,
    store_file_record, store_chunk, store_chunk_tags,
)
from .classification import classify_chunk
from .manifests import (
    write_repo_manifest, write_chunk_manifest,
    write_curated_artifact, update_repo_manifest_extraction_status,
)

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "searching": 99,
    "running": 100,
    "completed": 202,
    "succeed": 200,
    "failed": 404,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_run(
    conn: sqlite3.Connection,
    mode: str,
    label: str | None = None,
    description: str | None = None,
    run_inputs: dict | None = None,
) -> int:
    now = _now()
    inputs_json = json.dumps(run_inputs or {})
    cursor = conn.execute(
        """INSERT INTO runs (mode, label, description, run_inputs, status, status_code, started_at, created_at)
           VALUES (?, ?, ?, ?, 'searching', 99, ?, ?)""",
        (mode, label, description, inputs_json, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def update_run_status(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    code = STATUS_MAP.get(status, 99)
    updates = {"status": status, "status_code": code}
    if status in ("completed", "failed"):
        updates["finished_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE runs SET {sets} WHERE run_id=?", (*updates.values(), run_id))
    conn.commit()


def update_run_counts(conn: sqlite3.Connection, run_id: int) -> None:
    row = conn.execute(
        """SELECT
            COUNT(*) as total,
            COALESCE(SUM(CASE WHEN status='succeed' THEN 1 ELSE 0 END), 0) as ok,
            COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) as fail
           FROM run_items WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    rejected = conn.execute(
        "SELECT COUNT(*) as cnt FROM repo_rejections WHERE run_id=?", (run_id,)
    ).fetchone()["cnt"]
    conn.execute(
        "UPDATE runs SET total_items=?, succeeded=?, failed=?, rejected=? WHERE run_id=?",
        (row["total"], row["ok"], row["fail"], rejected, run_id),
    )
    conn.commit()


def create_run_item(conn: sqlite3.Connection, run_id: int, repo_full_name: str) -> int:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO run_items (run_id, repo_full_name, status, status_code, created_at)
           VALUES (?, ?, 'pending', 99, ?)""",
        (run_id, repo_full_name, now),
    )
    conn.commit()
    return cursor.lastrowid


def update_item_status(conn: sqlite3.Connection, item_id: int, status: str, error: str | None = None) -> None:
    code = STATUS_MAP.get(status, 99)
    now = _now()
    if status == "running":
        conn.execute(
            "UPDATE run_items SET status=?, status_code=?, started_at=? WHERE item_id=?",
            (status, code, now, item_id),
        )
    else:
        conn.execute(
            "UPDATE run_items SET status=?, status_code=?, error_message=?, finished_at=? WHERE item_id=?",
            (status, code, error, now, item_id),
        )
    conn.commit()


def record_failure(
    conn: sqlite3.Connection,
    run_id: int,
    item_id: int | None,
    repo_full_name: str | None,
    error_type: str,
    error_message: str,
    is_retryable: bool = False,
) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO failures (run_id, item_id, repo_full_name, error_type, error_message,
           is_retryable, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, item_id, repo_full_name, error_type, redact_token(error_message), 1 if is_retryable else 0, now),
    )
    conn.commit()


def upsert_repo(conn: sqlite3.Connection, repo: dict[str, Any], niche_id: str | None = None) -> int:
    now = _now()
    quality_score = repo.get("_quality_score")
    existing = conn.execute(
        "SELECT repo_id FROM repos WHERE full_name=?", (repo["full_name"],)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE repos SET default_branch=?, license=?, topics=?, languages=?,
               size_kb=?, stars=?, is_fork=?, is_archived=?, last_pushed_at=?,
               repo_quality_score=?, updated_at=?
               WHERE repo_id=?""",
            (
                repo.get("default_branch", "main"),
                repo.get("license"),
                json.dumps(repo.get("topics", [])),
                json.dumps(repo.get("languages", [])),
                repo.get("size_kb", 0),
                repo.get("stars", 0),
                1 if repo.get("is_fork") else 0,
                1 if repo.get("is_archived") else 0,
                repo.get("last_pushed_at"),
                quality_score,
                now,
                existing["repo_id"],
            ),
        )
        conn.commit()
        return existing["repo_id"]
    else:
        cursor = conn.execute(
            """INSERT INTO repos (owner, name, full_name, source_url, default_branch, license,
               topics, languages, size_kb, stars, is_fork, is_archived, last_pushed_at,
               discovered_via_niche, repo_quality_score, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo.get("owner", ""),
                repo.get("name", ""),
                repo["full_name"],
                repo.get("source_url", f"https://github.com/{repo['full_name']}"),
                repo.get("default_branch", "main"),
                repo.get("license"),
                json.dumps(repo.get("topics", [])),
                json.dumps(repo.get("languages", [])),
                repo.get("size_kb", 0),
                repo.get("stars", 0),
                1 if repo.get("is_fork") else 0,
                1 if repo.get("is_archived") else 0,
                repo.get("last_pushed_at"),
                niche_id,
                quality_score,
                now,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def update_repo_filesystem_quality(
    conn: sqlite3.Connection,
    repo_id: int,
    clone_path: Path,
) -> None:
    """Inspect a cloned repo's filesystem for quality signals and update the repos table.

    Detects: has_docs, has_tests, has_examples, generated_code_signal, maintenance_health.
    Called post-clone, before extraction. Non-fatal — failures are logged and silently skipped.
    """
    try:
        has_docs = _detect_has_docs(clone_path)
        has_tests = _detect_has_tests(clone_path)
        has_examples = _detect_has_examples(clone_path)
        generated_signal = _estimate_generated_code_signal(clone_path)
        health = _estimate_maintenance_health(clone_path)

        conn.execute(
            """UPDATE repos SET has_docs=?, has_tests=?, has_examples=?,
               generated_code_signal=?, maintenance_health=? WHERE repo_id=?""",
            (
                1 if has_docs else 0,
                1 if has_tests else 0,
                1 if has_examples else 0,
                generated_signal,
                health,
                repo_id,
            ),
        )
        conn.commit()
        logger.debug(
            "Filesystem quality: repo_id=%d docs=%s tests=%s examples=%s generated=%.2f health=%s",
            repo_id, has_docs, has_tests, has_examples, generated_signal, health,
        )
    except Exception as exc:
        logger.warning("Filesystem quality inspection failed for repo_id=%d: %s", repo_id, exc)


# ---------------------------------------------------------------------------
# Filesystem quality signal helpers
# ---------------------------------------------------------------------------

_DOC_DIRS = {"docs", "doc", "documentation", "wiki", "guides", "guide"}
_DOC_FILES = {"readme.md", "readme.rst", "readme.txt", "readme", "contributing.md", "changelog.md"}

_TEST_DIRS = {"tests", "test", "spec", "specs", "__tests__", "e2e", "integration"}
_TEST_PATTERNS = {"test_", "_test.", ".test.", ".spec.", "_spec."}

_EXAMPLE_DIRS = {"examples", "example", "demo", "demos", "samples", "sample", "tutorial", "tutorials"}

_GENERATED_MARKERS = {
    "generated", "auto-generated", "do not edit", "autogenerated",
    "this file was generated", "code generated by",
}


def _detect_has_docs(clone_path: Path) -> bool:
    for item in clone_path.iterdir():
        name_lower = item.name.lower()
        if item.is_dir() and name_lower in _DOC_DIRS:
            return True
        if item.is_file() and name_lower in _DOC_FILES:
            return True
    return False


def _detect_has_tests(clone_path: Path) -> bool:
    for item in clone_path.iterdir():
        name_lower = item.name.lower()
        if item.is_dir() and name_lower in _TEST_DIRS:
            return True
    # Also check for test files in src dirs
    for p in clone_path.rglob("*.py"):
        if any(pat in p.name.lower() for pat in _TEST_PATTERNS):
            return True
        break  # Found one — enough
    return False


def _detect_has_examples(clone_path: Path) -> bool:
    for item in clone_path.iterdir():
        if item.is_dir() and item.name.lower() in _EXAMPLE_DIRS:
            return True
    return False


def _estimate_generated_code_signal(clone_path: Path) -> float:
    """Sample up to 50 source files; return fraction with generated-code markers."""
    checked = 0
    generated = 0
    extensions = {".py", ".js", ".ts", ".go", ".java", ".rs"}
    for p in clone_path.rglob("*"):
        if not p.is_file() or p.suffix not in extensions:
            continue
        if any(part in {".git", "node_modules", "vendor", "__pycache__"} for part in p.parts):
            continue
        try:
            header = p.read_bytes()[:512].decode("utf-8", errors="replace").lower()
            if any(marker in header for marker in _GENERATED_MARKERS):
                generated += 1
        except OSError:
            pass
        checked += 1
        if checked >= 50:
            break
    return round(generated / checked, 3) if checked > 0 else 0.0


def _estimate_maintenance_health(clone_path: Path) -> str:
    """Estimate maintenance health from presence of CI config files."""
    ci_indicators = {
        ".github", ".travis.yml", ".circleci", "Jenkinsfile",
        ".gitlab-ci.yml", "azure-pipelines.yml", "tox.ini",
        "Makefile", "pyproject.toml", "package.json",
    }
    for item in clone_path.iterdir():
        if item.name in ci_indicators:
            return "active"
    return "unknown"


def create_snapshot(conn: sqlite3.Connection, repo_id: int, commit_sha: str, branch: str) -> int:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO repo_snapshots (repo_id, commit_sha, branch, snapshot_at, ingestion_status)
           VALUES (?, ?, ?, ?, 'completed')""",
        (repo_id, commit_sha, branch, now),
    )
    conn.commit()
    return cursor.lastrowid


def _get_discovery_settings(conn: sqlite3.Connection) -> dict:
    """Read discovery.* settings from DB, returning max_pages and per_page."""
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'discovery.%'"
    ).fetchall()
    result: dict = {}
    for r in rows:
        key = r["key"].replace("discovery.", "")
        val = r["value"]
        result[key] = int(val) if val.isdigit() else val
    return result


def execute_run(run_id: int, mode: str, repo_inputs: list[str] | None = None,
                niche_ids: list[str] | None = None, db_path: Path | None = None) -> None:
    """Execute a full run in a background thread. Opens its own DB connection."""
    conn = get_connection(db_path)
    credentials = load_credentials()
    disc_settings = _get_discovery_settings(conn)
    client = GitHubClient(
        credentials,
        max_pages=disc_settings.get("max_pages", 3),
        per_page=disc_settings.get("results_per_page", 30),
    )

    try:
        filter_settings = get_filter_settings(conn)
        update_run_status(conn, run_id, "searching")

        if mode == "manual_repo_list" and repo_inputs:
            _execute_manual_run(conn, client, credentials, run_id, repo_inputs, filter_settings)
        elif mode in ("niche_group", "scheduled") and niche_ids:
            _execute_niche_run(conn, client, credentials, run_id, niche_ids, filter_settings)
        else:
            update_run_status(conn, run_id, "failed")
            record_failure(conn, run_id, None, None, "invalid_input", f"Invalid mode={mode} or missing inputs")
            return

        update_run_counts(conn, run_id)
        update_run_status(conn, run_id, "completed")
    except Exception as e:
        logger.error("Run %d failed: %s", run_id, redact_token(str(e)))
        update_run_status(conn, run_id, "failed")
        record_failure(conn, run_id, None, None, "run_error", str(e))
        update_run_counts(conn, run_id)
    finally:
        conn.close()


def _execute_manual_run(conn, client, credentials, run_id, repo_inputs, filter_settings):
    update_run_status(conn, run_id, "running")
    resolved = resolve_manual_inputs(client, repo_inputs)

    for repo_data, original_input in resolved:
        try:
            full_name = repo_data.get("full_name", original_input)

            if repo_data.get("_not_found"):
                # Not found is a failure (resolution error), create item
                item_id = create_run_item(conn, run_id, full_name)
                update_item_status(conn, item_id, "failed", "Repository not found")
                record_failure(conn, run_id, item_id, full_name, "not_found", "Repository not found on GitHub")
                update_run_counts(conn, run_id)
                continue

            if repo_data.get("_error"):
                # Resolution error is a failure, create item
                item_id = create_run_item(conn, run_id, full_name)
                update_item_status(conn, item_id, "failed", repo_data["_error"])
                record_failure(conn, run_id, item_id, full_name, "resolve_error", repo_data["_error"])
                update_run_counts(conn, run_id)
                continue

            # Filter (no niche context for manual runs)
            accepted, reason, explanation = filter_candidate(repo_data, None, filter_settings)
            if not accepted:
                # Rejection is not a failure — don't create run_item
                record_rejection(conn, run_id, full_name, None, original_input, reason, explanation)
                update_run_counts(conn, run_id)
                continue

            # Only create run_item for accepted repos
            item_id = create_run_item(conn, run_id, full_name)
            update_run_counts(conn, run_id)  # show in-flight item in dashboard immediately
            _clone_and_record(conn, credentials, run_id, item_id, repo_data, None)
            update_run_counts(conn, run_id)  # update with final success/failed status
        except Exception as e:
            error_msg = redact_token(str(e))
            logger.error("Unexpected error processing %s: %s", original_input, error_msg)
            record_failure(conn, run_id, None, str(original_input), "processing_error", error_msg)
            update_run_counts(conn, run_id)


def _execute_niche_run(conn, client, credentials, run_id, niche_ids, filter_settings):
    candidates = discover_from_niches(client, conn, niche_ids)
    update_run_status(conn, run_id, "running")

    for repo_data, niche, query in candidates:
        try:
            full_name = repo_data["full_name"]

            accepted, reason, explanation = filter_candidate(repo_data, niche, filter_settings)
            if not accepted:
                # Rejection is not a failure — don't create run_item
                record_rejection(conn, run_id, full_name, niche.get("niche_id"), query, reason, explanation)
                update_run_counts(conn, run_id)
                continue

            # Only create run_item for accepted repos
            item_id = create_run_item(conn, run_id, full_name)
            update_run_counts(conn, run_id)  # show in-flight item in dashboard immediately
            _clone_and_record(conn, credentials, run_id, item_id, repo_data, niche.get("niche_id"))
            update_run_counts(conn, run_id)  # update with final success/failed status
        except Exception as e:
            error_msg = redact_token(str(e))
            logger.error("Unexpected error processing %s: %s", repo_data.get("full_name", "unknown"), error_msg)
            record_failure(conn, run_id, None, repo_data.get("full_name"), "processing_error", error_msg)
            update_run_counts(conn, run_id)


def _clone_and_record(conn, credentials, run_id, item_id, repo_data, niche_id):
    full_name = repo_data["full_name"]
    owner = repo_data.get("owner", "")
    update_item_status(conn, item_id, "running")

    try:
        clone_path, commit_sha = clone_or_update(full_name, credentials)

        repo_id = upsert_repo(conn, repo_data, niche_id)
        conn.execute("UPDATE repos SET clone_path=? WHERE repo_id=?", (str(clone_path), repo_id))
        conn.commit()

        # Filesystem quality inspection (non-fatal, runs post-clone before extraction)
        update_repo_filesystem_quality(conn, repo_id, clone_path)

        snapshot_id = create_snapshot(conn, repo_id, commit_sha, repo_data.get("default_branch", "main"))
        conn.execute(
            "UPDATE run_items SET repo_id=?, snapshot_id=? WHERE item_id=?",
            (repo_id, snapshot_id, item_id),
        )
        conn.commit()

        # Record storage organization (manifests/pointers only, not duplicate clones)
        from .storage import record_repo_organization
        record_repo_organization(
            full_name,
            clone_path,
            niche_id=niche_id,
            owner=owner if owner else None,
            run_id=run_id,
            is_grouped=niche_id is not None,
        )

        # Write canonical repo manifest
        try:
            _now_ts = _now()
            write_repo_manifest(
                repo_id=repo_id,
                snapshot_id=snapshot_id,
                full_name=full_name,
                owner=owner,
                name=repo_data.get("name", ""),
                source_url=repo_data.get("source_url", f"https://github.com/{full_name}"),
                default_branch=repo_data.get("default_branch", "main"),
                commit_sha=commit_sha,
                license=repo_data.get("license"),
                topics=repo_data.get("topics", []),
                languages=repo_data.get("languages", []),
                discovered_via_niche=niche_id,
                clone_path=str(clone_path),
                ingested_at=_now_ts,
                ingestion_status="completed",
                extraction_status="pending",
            )
        except Exception as exc:
            logger.warning("Repo manifest write failed for %s: %s", full_name, exc)

        # Run extraction pipeline (non-fatal)
        run_extraction_pipeline(conn, run_id, item_id, repo_id, snapshot_id, full_name, clone_path)

        # Run validation on the new snapshot's chunks (non-fatal)
        try:
            from .validation import validate_pending_chunks
            vr = validate_pending_chunks(snapshot_id=snapshot_id)
            logger.info(
                "Validation done for snapshot %d: %d accepted, %d quarantined",
                snapshot_id, vr.accepted, vr.quarantined,
            )
        except Exception as exc:
            logger.warning("Post-extraction validation failed for snapshot %d (non-fatal): %s", snapshot_id, exc)

        # Generate teaching artifacts for accepted chunks (non-fatal)
        try:
            from .teaching_artifacts import generate_teaching_artifacts
            tr = generate_teaching_artifacts(snapshot_id=snapshot_id)
            logger.info(
                "Teaching artifacts done for snapshot %d: %d generated, %d skipped",
                snapshot_id, tr.generated, tr.skipped_existing,
            )
        except Exception as exc:
            logger.warning("Teaching artifact generation failed for snapshot %d (non-fatal): %s", snapshot_id, exc)

        update_item_status(conn, item_id, "succeed")
    except CloneTimeoutError as e:
        error_msg = str(e)
        logger.warning("Clone timed out for %s: %s — skipping", full_name, error_msg)
        update_item_status(conn, item_id, "failed", error_msg)
        record_failure(conn, run_id, item_id, full_name, "clone_timeout", error_msg, is_retryable=True)
    except Exception as e:
        error_msg = redact_token(str(e))
        logger.error("Clone failed for %s: %s", full_name, error_msg)
        update_item_status(conn, item_id, "failed", error_msg)
        record_failure(conn, run_id, item_id, full_name, "clone_error", error_msg, is_retryable=True)


def run_extraction_pipeline(
    conn: sqlite3.Connection,
    run_id: int | None,
    item_id: int | None,
    repo_id: int,
    snapshot_id: int,
    full_name: str,
    clone_path: Path,
) -> None:
    """
    Inspect files, extract chunks, classify, persist records, and write artifacts.
    All failures are logged as non-fatal — they do not abort the run.
    """
    logger.info("Starting extraction for %s (snapshot %d)", full_name, snapshot_id)

    # Update extraction status to running
    try:
        conn.execute(
            "UPDATE repo_snapshots SET extraction_status='running' WHERE snapshot_id=?",
            (snapshot_id,),
        )
        conn.commit()
    except Exception:
        pass

    total_files = 0
    included_files = 0
    total_chunks = 0
    extraction_errors = 0

    try:
        for file_record in inspect_repo_files(clone_path):
            total_files += 1
            try:
                file_id = store_file_record(conn, snapshot_id, repo_id, file_record)
                conn.commit()
            except Exception as exc:
                logger.warning("Could not store file record %s: %s", file_record.relative_path, exc)
                continue

            if not file_record.included:
                continue

            included_files += 1

            # Extract chunks
            chunks = []
            try:
                chunks = extract_chunks(clone_path, file_record)
            except Exception as exc:
                logger.warning("Extraction failed for %s: %s", file_record.relative_path, exc)
                record_failure(
                    conn, run_id, item_id, full_name,
                    "extraction_error",
                    f"{file_record.relative_path}: {exc}",
                    is_retryable=False,
                )
                extraction_errors += 1
                continue

            for chunk in chunks:
                try:
                    # Classify
                    classification = classify_chunk(chunk)

                    # Store in DB
                    chunk_id = store_chunk(
                        conn, file_id, snapshot_id, repo_id, chunk,
                        {
                            "longevity_band": classification.longevity_band,
                            "longevity_confidence": classification.longevity_confidence,
                            "primary_tag": classification.primary_tag,
                            "summary": classification.summary,
                            "quality_flags": classification.quality_flags,
                            "difficulty": classification.difficulty,
                            "topic": classification.topic,
                            "subtopic": classification.subtopic,
                            "style": classification.style,
                            "paradigm": classification.paradigm,
                            "architecture_level": classification.architecture_level,
                            "security_relevance": classification.security_relevance,
                            "example_type": classification.example_type,
                            "beginner_safe": classification.beginner_safe,
                            "chunk_quality_score": classification.chunk_quality_score,
                            "teaching_value_score": classification.teaching_value_score,
                        },
                    )
                    store_chunk_tags(conn, chunk_id, classification.category_tags)
                    conn.commit()
                    total_chunks += 1

                    # Provenance reference
                    provenance_ref = f"{full_name}@{snapshot_id}:{file_record.relative_path}:{chunk.start_line}-{chunk.end_line}"

                    # Write chunk manifest
                    write_chunk_manifest(
                        chunk_id=chunk_id,
                        repo_id=repo_id,
                        snapshot_id=snapshot_id,
                        file_path=file_record.relative_path,
                        language=chunk.language,
                        chunk_type=chunk.chunk_type,
                        symbol_name=chunk.symbol_name,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content_hash=chunk.content_hash,
                        classification=classification,
                        provenance_ref=provenance_ref,
                    )

                    # Write curated artifact
                    write_curated_artifact(
                        chunk_id=chunk_id,
                        repo_id=repo_id,
                        snapshot_id=snapshot_id,
                        full_name=full_name,
                        file_path=file_record.relative_path,
                        chunk=chunk,
                        classification=classification,
                    )

                except Exception as exc:
                    logger.warning("Chunk processing failed for %s line %d: %s",
                                   file_record.relative_path, chunk.start_line, exc)
                    extraction_errors += 1

        # Mark extraction complete
        final_status = "completed" if extraction_errors == 0 else "completed_with_errors"
        conn.execute(
            "UPDATE repo_snapshots SET extraction_status=? WHERE snapshot_id=?",
            (final_status, snapshot_id),
        )
        conn.commit()

        # Update repo manifest with extraction results
        update_repo_manifest_extraction_status(
            full_name, snapshot_id, final_status, total_chunks, included_files
        )

        logger.info(
            "Extraction done for %s: %d files (%d included), %d chunks, %d errors",
            full_name, total_files, included_files, total_chunks, extraction_errors,
        )

    except Exception as exc:
        logger.error("Extraction pipeline crashed for %s: %s", full_name, exc)
        try:
            conn.execute(
                "UPDATE repo_snapshots SET extraction_status='failed' WHERE snapshot_id=?",
                (snapshot_id,),
            )
            conn.commit()
        except Exception:
            pass
        record_failure(conn, run_id, item_id, full_name, "extraction_pipeline_error", str(exc))


# ---------------------------------------------------------------------------
# Backfill and per-repo rerun
# ---------------------------------------------------------------------------

def backfill_pending_extraction(db_path: Path | None = None, limit: int = 200) -> dict:
    """
    Find snapshots with extraction_status='pending' whose repo has a valid clone_path
    and run extraction for each. Opens its own DB connection — safe to call from a thread.

    Returns a summary dict with counts.
    """
    conn = get_connection(db_path)
    queued = 0
    skipped_missing = 0
    errors = 0

    try:
        rows = conn.execute(
            """SELECT s.snapshot_id, s.repo_id, r.full_name, r.clone_path,
                      s.commit_sha, r.owner, r.name, r.source_url, r.default_branch,
                      r.license, r.topics, r.languages, r.discovered_via_niche
               FROM repo_snapshots s
               JOIN repos r ON r.repo_id = s.repo_id
               WHERE s.extraction_status = 'pending'
               ORDER BY s.snapshot_id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        for row in rows:
            clone_path = row["clone_path"]
            if not clone_path or not Path(clone_path).exists():
                logger.warning("Backfill: clone path missing or gone for %s — skipping", row["full_name"])
                conn.execute(
                    "UPDATE repo_snapshots SET extraction_status='skipped_no_clone' WHERE snapshot_id=?",
                    (row["snapshot_id"],),
                )
                conn.commit()
                record_failure(
                    conn, None, None, row["full_name"],
                    "backfill_no_clone_path",
                    f"Clone path missing or does not exist: {clone_path}",
                )
                skipped_missing += 1
                continue

            try:
                # Write repo manifest if not already present (best-effort)
                from .manifests import write_repo_manifest
                import json as _json
                try:
                    write_repo_manifest(
                        repo_id=row["repo_id"],
                        snapshot_id=row["snapshot_id"],
                        full_name=row["full_name"],
                        owner=row["owner"] or "",
                        name=row["name"] or "",
                        source_url=row["source_url"] or f"https://github.com/{row['full_name']}",
                        default_branch=row["default_branch"] or "main",
                        commit_sha=row["commit_sha"],
                        license=row["license"],
                        topics=_json.loads(row["topics"]) if row["topics"] else [],
                        languages=_json.loads(row["languages"]) if row["languages"] else [],
                        discovered_via_niche=row["discovered_via_niche"],
                        clone_path=clone_path,
                        ingested_at=_now(),
                        ingestion_status="completed",
                        extraction_status="pending",
                    )
                except Exception as exc:
                    logger.warning("Backfill: repo manifest write failed for %s: %s", row["full_name"], exc)

                run_extraction_pipeline(
                    conn,
                    run_id=None,
                    item_id=None,
                    repo_id=row["repo_id"],
                    snapshot_id=row["snapshot_id"],
                    full_name=row["full_name"],
                    clone_path=Path(clone_path),
                )
                queued += 1
            except Exception as exc:
                logger.error("Backfill failed for %s: %s", row["full_name"], exc)
                errors += 1

    finally:
        conn.close()

    logger.info("Backfill complete: %d extracted, %d skipped (no clone), %d errors", queued, skipped_missing, errors)
    return {"extracted": queued, "skipped_no_clone": skipped_missing, "errors": errors}


def run_repo_extraction(repo_id: int, db_path: Path | None = None) -> dict:
    """
    Run extraction for the latest snapshot of a single repo.
    Clears previous file/chunk records for that snapshot before rerunning.
    Opens its own DB connection — safe to call from a thread.
    """
    conn = get_connection(db_path)
    try:
        repo = conn.execute("SELECT * FROM repos WHERE repo_id=?", (repo_id,)).fetchone()
        if not repo:
            return {"status": "error", "message": f"Repo {repo_id} not found"}

        snapshot = conn.execute(
            "SELECT * FROM repo_snapshots WHERE repo_id=? ORDER BY snapshot_id DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        if not snapshot:
            return {"status": "error", "message": f"No snapshot found for repo {repo_id}"}

        clone_path = repo["clone_path"]
        if not clone_path or not Path(clone_path).exists():
            msg = f"Clone path missing or does not exist: {clone_path}"
            conn.execute(
                "UPDATE repo_snapshots SET extraction_status='skipped_no_clone' WHERE snapshot_id=?",
                (snapshot["snapshot_id"],),
            )
            conn.commit()
            record_failure(conn, None, None, repo["full_name"], "rerun_no_clone_path", msg)
            return {"status": "error", "message": msg}

        sid = snapshot["snapshot_id"]

        # Clear previous extraction data for this snapshot (clean rerun)
        chunk_ids = [r[0] for r in conn.execute(
            "SELECT chunk_id FROM chunks WHERE snapshot_id=?", (sid,)
        ).fetchall()]
        if chunk_ids:
            conn.execute(
                f"DELETE FROM chunk_tags WHERE chunk_id IN ({','.join('?' * len(chunk_ids))})",
                chunk_ids,
            )
        conn.execute("DELETE FROM chunks WHERE snapshot_id=?", (sid,))
        conn.execute("DELETE FROM files WHERE snapshot_id=?", (sid,))
        conn.commit()

        # Write repo manifest
        from .manifests import write_repo_manifest
        import json as _json
        try:
            write_repo_manifest(
                repo_id=repo_id,
                snapshot_id=sid,
                full_name=repo["full_name"],
                owner=repo["owner"] or "",
                name=repo["name"] or "",
                source_url=repo["source_url"] or f"https://github.com/{repo['full_name']}",
                default_branch=repo["default_branch"] or "main",
                commit_sha=snapshot["commit_sha"],
                license=repo["license"],
                topics=_json.loads(repo["topics"]) if repo["topics"] else [],
                languages=_json.loads(repo["languages"]) if repo["languages"] else [],
                discovered_via_niche=repo["discovered_via_niche"],
                clone_path=clone_path,
                ingested_at=_now(),
                ingestion_status="completed",
                extraction_status="pending",
            )
        except Exception as exc:
            logger.warning("Repo manifest write failed for %s: %s", repo["full_name"], exc)

        run_extraction_pipeline(
            conn,
            run_id=None,
            item_id=None,
            repo_id=repo_id,
            snapshot_id=sid,
            full_name=repo["full_name"],
            clone_path=Path(clone_path),
        )
        return {"status": "ok", "repo_id": repo_id, "snapshot_id": sid, "full_name": repo["full_name"]}
    finally:
        conn.close()


# Query helpers for the API layer

def get_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return serialize_rows(rows)


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return serialize_row(row)


def get_run_items(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM run_items WHERE run_id=? ORDER BY created_at", (run_id,)
    ).fetchall()
    return serialize_rows(rows)


def get_run_failures(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM failures WHERE run_id=? ORDER BY created_at", (run_id,)
    ).fetchall()
    return serialize_rows(rows)


def get_run_rejections(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM repo_rejections WHERE run_id=? ORDER BY created_at", (run_id,)
    ).fetchall()
    return serialize_rows(rows)


def get_all_failures(conn: sqlite3.Connection, hours: int | None = None) -> list[dict]:
    """Get failures, optionally filtered by retention hours."""
    if hours is None:
        # Get retention setting from database
        from .settings import get_setting
        retention_str = get_setting(conn, "log.retention_hours")
        hours = int(retention_str) if retention_str else 168

    if hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = conn.execute(
            "SELECT * FROM failures WHERE created_at >= ? ORDER BY created_at DESC LIMIT 500",
            (cutoff.isoformat(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM failures ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
    return serialize_rows(rows)


def get_all_rejections(conn: sqlite3.Connection, hours: int | None = None) -> list[dict]:
    """Get rejections, optionally filtered by retention hours."""
    if hours is None:
        # Get retention setting from database
        from .settings import get_setting
        retention_str = get_setting(conn, "log.retention_hours")
        hours = int(retention_str) if retention_str else 168

    if hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = conn.execute(
            "SELECT * FROM repo_rejections WHERE created_at >= ? ORDER BY created_at DESC LIMIT 500",
            (cutoff.isoformat(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM repo_rejections ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
    return serialize_rows(rows)


# Run control functions

def can_retry_run(run_status: str) -> bool:
    """Check if a run can be retried."""
    return run_status in ("completed", "failed")


def can_stop_run(run_status: str) -> bool:
    """Check if a run can be stopped."""
    return run_status in ("searching", "running")


def can_remove_run(run_status: str) -> bool:
    """Check if a run can be removed."""
    return run_status in ("completed", "failed")


def remove_run(conn: sqlite3.Connection, run_id: int) -> tuple[bool, str | None]:
    """Remove a run and its associated data. Returns (success, error_msg)."""
    run = get_run(conn, run_id)
    if not run:
        return False, "Run not found"

    if not can_remove_run(run["status"]):
        return False, f"Cannot remove run in status {run['status']}"

    try:
        # Delete associated records (cascading)
        conn.execute("DELETE FROM run_items WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM failures WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM repo_rejections WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        conn.commit()
        return True, None
    except Exception as e:
        return False, str(e)


# Dashboard helpers

def get_current_and_recent_runs(conn: sqlite3.Connection, limit: int = 20) -> tuple[dict | None, list[dict]]:
    """Get current active run and recent runs history.

    Current run = most recent run with status 'searching' or 'running'
    Recent runs = all other runs (excluding current), newest first
    """
    all_runs = get_runs(conn, limit=limit + 1)

    current_run = None
    for run in all_runs:
        if run["status"] in ("searching", "running"):
            current_run = run
            break

    # Recent runs = all except current
    if current_run:
        recent_runs = [r for r in all_runs if r["run_id"] != current_run["run_id"]]
    else:
        recent_runs = all_runs

    return current_run, recent_runs

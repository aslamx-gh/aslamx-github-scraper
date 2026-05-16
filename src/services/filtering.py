"""Deterministic repo filtering with rejection recording."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def get_filter_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'filter.%'"
    ).fetchall()
    settings = {}
    for r in rows:
        key = r["key"].replace("filter.", "")
        val = r["value"]
        if val in ("true", "false"):
            settings[key] = val == "true"
        elif val.isdigit():
            settings[key] = int(val)
        else:
            settings[key] = val
    return settings


def filter_candidate(
    repo: dict[str, Any],
    niche: dict[str, Any] | None,
    settings: dict[str, Any],
) -> tuple[bool, str | None, str | None]:
    """Returns (accepted, reason_code, explanation). If accepted, reason fields are None."""

    # Fork check
    exclude_forks = settings.get("exclude_forks", True)
    if niche:
        exclude_forks = niche.get("exclude_forks", exclude_forks)
    if exclude_forks and repo.get("is_fork"):
        return False, "fork_excluded", "Repository is a fork"

    # Archived check
    if settings.get("exclude_archived", True) and repo.get("is_archived"):
        return False, "archived_excluded", "Repository is archived"

    # License check
    if settings.get("require_license", True) and not repo.get("license"):
        return False, "license_missing", "No license detected"

    # Allowed licenses (niche-level override)
    allowed = []
    if niche:
        allowed = niche.get("allowed_licenses", [])
        if isinstance(allowed, str):
            try:
                allowed = json.loads(allowed)
            except (json.JSONDecodeError, ValueError):
                allowed = []
    if allowed and repo.get("license") and repo["license"] not in allowed:
        return False, "license_disallowed", f"License {repo['license']} not in allowed list"

    # Size check
    max_size = settings.get("max_repo_size_kb", 512000)
    if niche:
        max_size = niche.get("max_repo_size_kb", max_size)
    if repo.get("size_kb", 0) > max_size:
        return False, "repo_too_large", f"Repo size {repo['size_kb']}KB exceeds {max_size}KB limit"

    # Language check
    niche_langs = []
    if niche:
        langs = niche.get("languages", [])
        if isinstance(langs, str):
            try:
                niche_langs = json.loads(langs)
            except (json.JSONDecodeError, ValueError):
                niche_langs = []
        else:
            niche_langs = langs
    if niche_langs:
        repo_langs = repo.get("languages", [])
        if isinstance(repo_langs, str):
            try:
                repo_langs = json.loads(repo_langs)
            except (json.JSONDecodeError, ValueError):
                repo_langs = []
        if not set(repo_langs) & set(niche_langs):
            return (
                False,
                "language_mismatch",
                f"Repo languages {repo_langs} don't match niche {niche_langs}",
            )

    # Activity threshold
    min_days = settings.get("min_recent_activity_days", 0)
    if niche:
        min_days = niche.get("min_recent_activity_days", min_days)
    if min_days and repo.get("last_pushed_at"):
        try:
            pushed = datetime.fromisoformat(repo["last_pushed_at"].replace("Z", "+00:00"))
            cutoff = datetime.now(timezone.utc) - timedelta(days=min_days)
            if pushed < cutoff:
                return (
                    False,
                    "activity_below_threshold",
                    f"Last push {repo['last_pushed_at']} is older than {min_days} days",
                )
        except (ValueError, TypeError):
            pass

    # Stars check
    min_stars = settings.get("min_stars", 0)
    if niche:
        min_stars = niche.get("min_stars", min_stars)
    if min_stars and repo.get("stars", 0) < min_stars:
        return (
            False,
            "below_min_stars",
            f"Repo has {repo.get('stars', 0)} stars, minimum is {min_stars}",
        )

    # Exclude terms check (niche-level: match against repo full_name and description)
    if niche:
        exclude_terms = niche.get("exclude_terms", [])
        if isinstance(exclude_terms, str):
            try:
                exclude_terms = json.loads(exclude_terms)
            except (json.JSONDecodeError, ValueError):
                exclude_terms = []
        if exclude_terms:
            repo_name_lower = repo.get("full_name", "").lower()
            repo_desc_lower = (repo.get("description") or "").lower()
            for term in exclude_terms:
                term_lower = term.lower()
                if term_lower in repo_name_lower or term_lower in repo_desc_lower:
                    return False, "exclude_term_match", f"Repo matches excluded term '{term}'"

    # Teaching quality score check (V2)
    # Global threshold from settings; niche can override with a lower or higher value.
    # Quality score is attached to the repo dict by the discovery layer (_quality_score key).
    min_quality = float(settings.get("quality.min_score", 0.0))
    if niche:
        niche_min = niche.get("min_repo_quality_score")
        if niche_min is not None:
            try:
                min_quality = float(niche_min)
            except (TypeError, ValueError):
                pass
    if min_quality > 0.0:
        quality_score = repo.get("_quality_score")
        if quality_score is not None and quality_score < min_quality:
            return (
                False,
                "below_teaching_quality",
                f"Repo quality score {quality_score:.3f} below threshold {min_quality:.3f}",
            )

    return True, None, None


def record_rejection(
    conn: sqlite3.Connection,
    run_id: int,
    repo_full_name: str,
    source_niche: str | None,
    source_query: str | None,
    reason_code: str,
    explanation: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO repo_rejections (run_id, repo_full_name, source_niche, source_query,
           reason_code, explanation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, repo_full_name, source_niche, source_query, reason_code, explanation, now),
    )
    conn.commit()

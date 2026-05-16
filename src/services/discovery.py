"""Repository discovery — expand niches into candidate lists."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..github.client import GitHubClient, score_repo_quality_from_api

logger = logging.getLogger(__name__)


def discover_from_niches(
    client: GitHubClient,
    conn: sqlite3.Connection,
    niche_ids: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Discover repos from niche search queries.
    Returns list of (repo_data, niche_row_dict, query) tuples, deduplicated by full_name.
    """
    candidates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    seen: set[str] = set()

    for niche_id in niche_ids:
        niche_row = conn.execute(
            "SELECT * FROM niches WHERE niche_id = ? AND enabled = 1", (niche_id,)
        ).fetchone()
        if not niche_row:
            logger.warning("Niche %s not found or disabled", niche_id)
            continue

        niche = dict(niche_row)
        queries = niche.get("github_search_queries", "[]")
        if isinstance(queries, str):
            queries = json.loads(queries)

        for query in queries:
            logger.info("Searching: %s (niche=%s)", query, niche_id)
            try:
                repos = client.search_repos(query)
                for repo in repos:
                    fn = repo["full_name"]
                    if fn not in seen:
                        seen.add(fn)
                        _attach_quality_signals(repo)
                        candidates.append((repo, niche, query))
            except Exception as e:
                logger.error("Discovery error niche=%s query=%s: %s", niche_id, query, e)

        # Also search by github_topics if defined
        topics = niche.get("github_topics", "[]")
        if isinstance(topics, str):
            topics = json.loads(topics)
        for topic in topics:
            topic_query = f"topic:{topic}"
            logger.info("Searching topic: %s (niche=%s)", topic, niche_id)
            try:
                repos = client.search_repos(topic_query)
                for repo in repos:
                    fn = repo["full_name"]
                    if fn not in seen:
                        seen.add(fn)
                        _attach_quality_signals(repo)
                        candidates.append((repo, niche, topic_query))
            except Exception as e:
                logger.error("Discovery error niche=%s topic=%s: %s", niche_id, topic, e)

    logger.info("Discovered %d unique candidates from %d niches", len(candidates), len(niche_ids))
    return candidates


def resolve_manual_inputs(
    client: GitHubClient,
    repo_inputs: list[str],
) -> list[tuple[dict[str, Any], str]]:
    """Resolve manual repo inputs (owner/repo or URLs) to repo data.
    Returns list of (repo_data, original_input) tuples.
    """
    results: list[tuple[dict[str, Any], str]] = []

    for raw in repo_inputs:
        full_name = _parse_repo_input(raw)
        if not full_name:
            logger.warning("Could not parse repo input: %s", raw)
            continue

        try:
            repo = client.get_repo(full_name)
            if repo:
                _attach_quality_signals(repo)
                results.append((repo, raw))
            else:
                logger.warning("Repo not found: %s", full_name)
                results.append(({"full_name": full_name, "_not_found": True}, raw))
        except Exception as e:
            logger.error("Error resolving %s: %s", full_name, e)
            results.append(({"full_name": full_name, "_error": str(e)}, raw))

    return results


def _attach_quality_signals(repo: dict[str, Any]) -> None:
    """Score the repo using API metadata and attach signals directly to the repo dict."""
    signals = score_repo_quality_from_api(repo)
    repo["_quality_score"] = signals["quality_score"]
    repo["_quality_signals"] = signals


def _parse_repo_input(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    # Full URL: https://github.com/owner/repo or https://github.com/owner/repo.git
    if "github.com/" in raw:
        parts = raw.split("github.com/")[-1]
        parts = parts.rstrip("/").removesuffix(".git")
        segments = parts.split("/")
        if len(segments) >= 2:
            return f"{segments[0]}/{segments[1]}"
        return None
    # owner/repo format
    if "/" in raw:
        segments = raw.split("/")
        if len(segments) == 2 and segments[0] and segments[1]:
            return raw
    return None

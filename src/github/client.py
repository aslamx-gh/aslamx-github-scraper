"""GitHub REST API client for discovery and diagnostics."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .auth import GitHubCredentials, redact_token

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"


class GitHubAuthError(Exception):
    pass


class GitHubRateLimitError(Exception):
    pass


@dataclass
class RateLimitState:
    limit: int = 0
    remaining: int = 0
    reset_at: int = 0


@dataclass
class ConnectivityStatus:
    reachable: bool = False
    authenticated: bool = False
    auth_mode: str = "anonymous"
    login: str | None = None
    rate_limit: RateLimitState = field(default_factory=RateLimitState)
    error: str | None = None


class GitHubClient:
    def __init__(self, credentials: GitHubCredentials, max_pages: int = 3, per_page: int = 30):
        self.credentials = credentials
        self.max_pages = max_pages
        self.per_page = per_page
        self._rate_limit = RateLimitState()

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "aslamx-general-scraper/v1",
        }
        h.update(self.credentials.auth_header())
        return h

    def _update_rate_limit(self, response: httpx.Response) -> None:
        self._rate_limit.limit = int(response.headers.get("X-RateLimit-Limit", 0))
        self._rate_limit.remaining = int(response.headers.get("X-RateLimit-Remaining", 0))
        self._rate_limit.reset_at = int(response.headers.get("X-RateLimit-Reset", 0))

    def _handle_rate_limit(self) -> None:
        if self._rate_limit.remaining > 0:
            return
        wait = max(0, self._rate_limit.reset_at - int(time.time()))
        if wait > 120:
            raise GitHubRateLimitError(
                f"Rate limit exceeded. Resets in {wait}s (> 120s max wait)."
            )
        if wait > 0:
            logger.warning("Rate limit hit, sleeping %ds", wait)
            time.sleep(wait)

    def check_connectivity(self) -> ConnectivityStatus:
        status = ConnectivityStatus(auth_mode=self.credentials.mode)
        # Short timeouts so diagnostics fail fast rather than blocking the event loop.
        timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=1.0)
        try:
            if self.credentials.mode == "pat":
                r = httpx.get(f"{API_BASE}/user", headers=self._headers(), timeout=timeout)
                self._update_rate_limit(r)
                if r.status_code == 200:
                    data = r.json()
                    status.reachable = True
                    status.authenticated = True
                    status.login = data.get("login")
                elif r.status_code == 401:
                    status.reachable = True
                    status.authenticated = False
                    status.error = "Invalid token"
                else:
                    status.reachable = True
                    status.error = f"Unexpected status: {r.status_code}"
            else:
                r = httpx.get(f"{API_BASE}/rate_limit", headers=self._headers(), timeout=timeout)
                self._update_rate_limit(r)
                status.reachable = r.status_code == 200
                if not status.reachable:
                    status.error = f"Status: {r.status_code}"
            status.rate_limit = RateLimitState(
                limit=self._rate_limit.limit,
                remaining=self._rate_limit.remaining,
                reset_at=self._rate_limit.reset_at,
            )
        except httpx.HTTPError as e:
            status.error = redact_token(str(e))
        return status

    def search_repos(self, query: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for page in range(1, self.max_pages + 1):
            self._handle_rate_limit()
            try:
                r = httpx.get(
                    f"{API_BASE}/search/repositories",
                    params={"q": query, "per_page": self.per_page, "page": page, "sort": "stars"},
                    headers=self._headers(),
                    timeout=30,
                )
                self._update_rate_limit(r)

                if r.status_code == 401:
                    raise GitHubAuthError("Authentication failed")
                if r.status_code == 403:
                    raise GitHubRateLimitError("Forbidden — likely rate limited")
                if r.status_code != 200:
                    logger.warning("Search returned %d for query=%s", r.status_code, query)
                    break

                data = r.json()
                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    full_name = item.get("full_name", "")
                    if full_name and full_name not in seen:
                        seen.add(full_name)
                        results.append(_normalize_repo(item))

            except (httpx.HTTPError, GitHubAuthError, GitHubRateLimitError):
                raise
            except Exception as e:
                logger.error("Search error page=%d query=%s: %s", page, query, redact_token(str(e)))
                break

        return results

    def get_repo(self, full_name: str) -> dict[str, Any] | None:
        self._handle_rate_limit()
        try:
            r = httpx.get(
                f"{API_BASE}/repos/{full_name}",
                headers=self._headers(),
                timeout=15,
            )
            self._update_rate_limit(r)
            if r.status_code == 200:
                return _normalize_repo(r.json())
            if r.status_code == 404:
                return None
            if r.status_code == 401:
                raise GitHubAuthError("Authentication failed")
        except (httpx.HTTPError, GitHubAuthError, GitHubRateLimitError):
            raise
        except Exception as e:
            logger.error("get_repo error: %s", redact_token(str(e)))
        return None


def score_repo_quality_from_api(repo: dict[str, Any]) -> dict[str, Any]:
    """Score a repo's teaching quality using only GitHub API metadata.

    Returns a dict with individual signal flags and a composite quality_score (0.0–1.0).
    Signals are deterministic from the normalized repo dict produced by _normalize_repo().

    Scoring rationale (each signal contributes a weight):
    - has_description  (0.15) — well-described repos are more teachable
    - has_topics       (0.15) — topically organised repos indicate intentional structure
    - topics_count     (0.05 per topic, max 0.15) — more topics = richer context
    - has_license      (0.20) — license presence is required for safe teaching use
    - stars_signal     (0.15) — community validation; capped at 500 stars for full credit
    - not_trivially_tiny (0.10) — repos < 10 KB are usually toy or stub projects
    - not_oversized    (0.10) — repos > 200 MB signal generated/vendor-heavy content
    """
    score = 0.0
    signals: dict[str, Any] = {}

    # Description present
    desc = (repo.get("description") or "").strip()
    signals["has_description"] = bool(desc)
    if signals["has_description"]:
        score += 0.15

    # Topics present
    topics = repo.get("topics") or []
    signals["has_topics"] = len(topics) > 0
    signals["topics_count"] = len(topics)
    if signals["has_topics"]:
        score += 0.15
    # Bonus for richer topic coverage (up to 3 extra topics)
    score += min(len(topics), 3) * 0.05

    # License present
    signals["has_license"] = bool(repo.get("license"))
    if signals["has_license"]:
        score += 0.20

    # Stars signal — log-scaled, full credit at 500 stars
    stars = repo.get("stars", 0) or 0
    signals["stars"] = stars
    if stars >= 500:
        score += 0.15
    elif stars > 0:
        import math
        score += 0.15 * (math.log(stars + 1) / math.log(501))

    # Size signals
    size_kb = repo.get("size_kb", 0) or 0
    signals["size_kb"] = size_kb
    signals["not_trivially_tiny"] = size_kb >= 10
    signals["not_oversized"] = size_kb <= 204800  # 200 MB
    if signals["not_trivially_tiny"]:
        score += 0.10
    if signals["not_oversized"]:
        score += 0.10

    quality_score = round(min(score, 1.0), 3)
    signals["quality_score"] = quality_score
    return signals


def _normalize_repo(item: dict[str, Any]) -> dict[str, Any]:
    license_info = item.get("license")
    license_key = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    return {
        "full_name": item.get("full_name", ""),
        "owner": item.get("owner", {}).get("login", ""),
        "name": item.get("name", ""),
        "source_url": item.get("html_url", ""),
        "default_branch": item.get("default_branch", "main"),
        "license": license_key,
        "topics": item.get("topics", []),
        "languages": [item["language"]] if item.get("language") else [],
        "size_kb": item.get("size", 0),
        "stars": item.get("stargazers_count", 0),
        "is_fork": item.get("fork", False),
        "is_archived": item.get("archived", False),
        "last_pushed_at": item.get("pushed_at"),
        "description": item.get("description", ""),
    }

"""Git clone/update operations with token redaction.

CRITICAL: GITHUB_TOKEN is never persisted to .git/config, manifests, or logs.
- Token is only in subprocess memory during initial clone.
- Updates use the stored remote URL (without token) and authenticate via PAT env variable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .auth import GitHubCredentials, redact_token

logger = logging.getLogger(__name__)

DATA_REPOS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "repos"

# Hard timeout constants — change here to affect both clone and update paths.
CLONE_TIMEOUT_SECONDS = 300  # 5 minutes for initial shallow clone
UPDATE_TIMEOUT_SECONDS = 300  # 5 minutes for fetch
RESET_TIMEOUT_SECONDS = 60   # 1 minute for hard reset
SHA_TIMEOUT_SECONDS = 10     # 10 seconds to read HEAD SHA


class CloneTimeoutError(RuntimeError):
    """Raised when a git clone or fetch exceeds the configured timeout."""


def clone_or_update(
    full_name: str,
    credentials: GitHubCredentials,
    repos_dir: Path | None = None,
) -> tuple[Path, str]:
    """Clone or update a repo. Returns (clone_path, commit_sha).

    Token is passed only to subprocess, never written to disk or .git/config.
    """
    base = repos_dir or DATA_REPOS_DIR
    base.mkdir(parents=True, exist_ok=True)
    clone_path = base / full_name.replace("/", "_")

    https_url = f"https://github.com/{full_name}.git"

    if clone_path.exists() and (clone_path / ".git").exists():
        return _update(clone_path, credentials)
    elif clone_path.exists():
        logger.warning("Corrupt clone at %s, removing and re-cloning", clone_path)
        shutil.rmtree(clone_path)

    return _clone(clone_path, full_name, https_url, credentials)


def _clone(
    clone_path: Path, full_name: str, https_url: str, credentials: GitHubCredentials
) -> tuple[Path, str]:
    """Clone using token-embedded URL. Token stays in process memory only."""
    env = _build_auth_env(credentials)

    # Use embedded token only for this clone command
    auth_url = credentials.clone_url_with_auth(https_url)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, str(clone_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(redact_token(f"Clone failed: {e.stderr}")) from e
    except subprocess.TimeoutExpired:
        # Clean up partial clone directory so future runs start fresh
        if clone_path.exists():
            shutil.rmtree(clone_path, ignore_errors=True)
        raise CloneTimeoutError(f"Clone timed out after {CLONE_TIMEOUT_SECONDS}s")

    sha = _get_head_sha(clone_path, env)
    return clone_path, sha


def _update(clone_path: Path, credentials: GitHubCredentials) -> tuple[Path, str]:
    """Update using stored remote URL. Token passed via env, never stored in config."""
    env = _build_auth_env(credentials)

    try:
        subprocess.run(
            ["git", "-C", str(clone_path), "fetch", "--depth", "1", "origin"],
            env=env,
            capture_output=True,
            text=True,
            timeout=UPDATE_TIMEOUT_SECONDS,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_path), "reset", "--hard", "FETCH_HEAD"],
            env=env,
            capture_output=True,
            text=True,
            timeout=RESET_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(redact_token(f"Update failed: {e.stderr}")) from e
    except subprocess.TimeoutExpired:
        raise CloneTimeoutError(f"Update timed out after {UPDATE_TIMEOUT_SECONDS}s")

    sha = _get_head_sha(clone_path, env)
    return clone_path, sha


def _build_auth_env(credentials: GitHubCredentials) -> dict:
    """Build environment for git auth. Token never written to disk."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    # For PAT auth, set GitHub username/token for HTTPS
    # Git will use these for authentication without storing in config
    if credentials.mode == "pat" and credentials.token:
        # These environment variables are used by git for HTTP basic auth
        # They are NOT persisted to .git/config
        env["GIT_USERNAME"] = "x-access-token"
        env["GIT_PASSWORD"] = credentials.token

    return env


def _get_head_sha(clone_path: Path, env: dict) -> str:
    """Get current HEAD commit SHA."""
    result = subprocess.run(
        ["git", "-C", str(clone_path), "rev-parse", "HEAD"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()

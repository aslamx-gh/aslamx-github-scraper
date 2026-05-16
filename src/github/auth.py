"""GitHub credential management — environment only, never persisted."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GitHubCredentials:
    mode: str  # "pat" or "anonymous"
    token: str | None = None

    def auth_header(self) -> dict[str, str]:
        if self.mode == "pat" and self.token:
            return {"Authorization": f"token {self.token}"}
        return {}

    def clone_url_with_auth(self, https_url: str) -> str:
        if self.mode == "pat" and self.token:
            return https_url.replace(
                "https://github.com/",
                f"https://x-access-token:{self.token}@github.com/",
            )
        return https_url


def load_credentials() -> GitHubCredentials:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return GitHubCredentials(mode="pat", token=token)
    return GitHubCredentials(mode="anonymous")


TOKEN_PATTERN = re.compile(r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,})")


def redact_token(text: str) -> str:
    return TOKEN_PATTERN.sub("***REDACTED***", text)

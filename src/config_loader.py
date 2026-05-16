"""Load niche configuration from YAML files under config/niches/."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

NICHE_DIR = Path(__file__).resolve().parent.parent / "config" / "niches"

REQUIRED_FIELDS = {"niche_id", "title", "description", "languages", "github_search_queries"}

DEFAULTS = {
    "github_topics": [],
    "exclude_terms": [],
    "min_stars": 0,
    "max_repo_size_kb": 512000,
    "min_recent_activity_days": 0,
    "allowed_licenses": [],
    "exclude_forks": True,
}


def load_niche(path: Path) -> dict[str, Any]:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid niche file: {path}")
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"Missing required fields in {path}: {missing}")
    for key, default in DEFAULTS.items():
        data.setdefault(key, default)
    return data


def load_all_niches(niche_dir: Path | None = None) -> list[dict[str, Any]]:
    d = niche_dir or NICHE_DIR
    if not d.is_dir():
        return []
    niches = []
    for f in sorted(d.glob("*.yaml")):
        niches.append(load_niche(f))
    for f in sorted(d.glob("*.yml")):
        niches.append(load_niche(f))
    return niches

"""Organized storage views — manifests and pointers, not duplicate clones."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
REPOS_DIR = DATA_DIR / "repos"
REPOS_SPECIFIC = REPOS_DIR / "repos-specific-storage"
MULTIPLE_SPECIFIC = REPOS_DIR / "multiple-specific-storage"
QUARANTINE_DIR = DATA_DIR / "quarantine"


def ensure_storage_dirs() -> None:
    """Create organized storage directory structure."""
    for d in [
        REPOS_SPECIFIC / "categories",
        REPOS_SPECIFIC / "niches",
        REPOS_SPECIFIC / "authors",
        REPOS_SPECIFIC / "manual",
        MULTIPLE_SPECIFIC / "niches",
        MULTIPLE_SPECIFIC / "categories",
        MULTIPLE_SPECIFIC / "authors",
        MULTIPLE_SPECIFIC / "grouped-runs",
    ]:
        d.mkdir(parents=True, exist_ok=True)
    logger.info("Storage directories ensured")


def record_repo_organization(
    full_name: str,
    clone_path: Path,
    niche_id: str | None = None,
    owner: str | None = None,
    run_id: int | None = None,
    is_grouped: bool = False,
) -> None:
    """Record repo organization using manifest pointers and metadata.

    Does NOT duplicate the clone. Just creates metadata and references.
    """
    # Single-repo organization
    if niche_id:
        _record_niche_org(full_name, clone_path, niche_id)
    if owner:
        _record_author_org(full_name, clone_path, owner)
    if not niche_id and not is_grouped:
        _record_manual_org(full_name, clone_path)

    # Multi-repo organization
    if is_grouped and niche_id and run_id:
        _record_grouped_org(full_name, clone_path, niche_id, run_id)


def _record_niche_org(full_name: str, clone_path: Path, niche_id: str) -> None:
    """Create niche organization manifest pointer."""
    niche_dir = REPOS_SPECIFIC / "niches" / niche_id
    niche_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "full_name": full_name,
        "clone_path": str(clone_path),
        "niche_id": niche_id,
    }

    pointer_file = niche_dir / f"{full_name.replace('/', '_')}.json"
    pointer_file.write_text(json.dumps(manifest, indent=2))


def _record_author_org(full_name: str, clone_path: Path, owner: str) -> None:
    """Create author organization manifest pointer."""
    author_dir = REPOS_SPECIFIC / "authors" / owner
    author_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "full_name": full_name,
        "clone_path": str(clone_path),
        "owner": owner,
    }

    pointer_file = author_dir / f"{full_name.replace('/', '_')}.json"
    pointer_file.write_text(json.dumps(manifest, indent=2))


def _record_manual_org(full_name: str, clone_path: Path) -> None:
    """Create manual run organization manifest pointer."""
    manual_dir = REPOS_SPECIFIC / "manual"

    manifest = {
        "full_name": full_name,
        "clone_path": str(clone_path),
        "origin": "manual",
    }

    pointer_file = manual_dir / f"{full_name.replace('/', '_')}.json"
    pointer_file.write_text(json.dumps(manifest, indent=2))


def _record_grouped_org(
    full_name: str, clone_path: Path, niche_id: str, run_id: int
) -> None:
    """Create grouped run organization manifest pointer."""
    run_dir = MULTIPLE_SPECIFIC / "grouped-runs" / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    niche_dir = MULTIPLE_SPECIFIC / "niches" / niche_id
    niche_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "full_name": full_name,
        "clone_path": str(clone_path),
        "niche_id": niche_id,
        "run_id": run_id,
    }

    pointer_file = run_dir / f"{full_name.replace('/', '_')}.json"
    pointer_file.write_text(json.dumps(manifest, indent=2))

    niche_pointer = niche_dir / f"run_{run_id}_{full_name.replace('/', '_')}.json"
    niche_pointer.write_text(json.dumps(manifest, indent=2))


def write_quarantine_artifact(
    chunk_id: int,
    content_hash: str,
    quarantine_reason: str,
) -> None:
    """Write a lightweight quarantine record to data/quarantine/.

    Creates one JSON file per quarantined chunk for operator inspection.
    Does NOT move or delete the original curated artifact.
    """
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "chunk_id": chunk_id,
        "content_hash": content_hash,
        "quarantine_reason": quarantine_reason,
    }
    pointer = QUARANTINE_DIR / f"chunk_{chunk_id}.json"
    pointer.write_text(json.dumps(record, indent=2))
    logger.debug("Wrote quarantine record: %s", pointer)


def get_repo_manifests(organization: str = "all") -> list[dict]:
    """Get repo manifest pointers by organization type."""
    manifests = []

    def scan_dir(base_dir):
        for json_file in base_dir.rglob("*.json"):
            try:
                data = json.loads(json_file.read_text())
                manifests.append(data)
            except Exception as e:
                logger.error("Failed to read manifest %s: %s", json_file, e)

    if organization in ("all", "repos_specific"):
        scan_dir(REPOS_SPECIFIC)
    if organization in ("all", "multiple_specific"):
        scan_dir(MULTIPLE_SPECIFIC)

    return manifests

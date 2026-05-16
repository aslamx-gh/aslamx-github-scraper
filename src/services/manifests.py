"""Canonical manifest writing and curated artifact output for repos and chunks."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .classification import Classification
    from .extraction import Chunk

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
MANIFESTS_REPOS = DATA_DIR / "manifests" / "repos"
MANIFESTS_CHUNKS = DATA_DIR / "manifests" / "chunks"
EXTRACTED_DIR = DATA_DIR / "extracted"
TEACHING_DIR = DATA_DIR / "teaching"

VALID_BANDS = {"small", "medium", "high", "very-high"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(s: str) -> str:
    """Convert arbitrary string to safe filesystem name."""
    return re.sub(r"[^\w\-.]", "_", s)


# ---------------------------------------------------------------------------
# Repo manifest
# ---------------------------------------------------------------------------

def write_repo_manifest(
    *,
    repo_id: int,
    snapshot_id: int,
    full_name: str,
    owner: str,
    name: str,
    source_url: str,
    default_branch: str,
    commit_sha: str,
    license: str | None,
    topics: list[str],
    languages: list[str],
    discovered_via_niche: str | None,
    clone_path: str,
    ingested_at: str,
    ingestion_status: str,
    extraction_status: str = "pending",
    notes: str | None = None,
) -> Path:
    """Write a canonical repo manifest JSON to data/manifests/repos/."""
    MANIFESTS_REPOS.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "repo_id": repo_id,
        "snapshot_id": snapshot_id,
        "source_url": source_url,
        "owner": owner,
        "name": name,
        "full_name": full_name,
        "default_branch": default_branch,
        "commit_sha": commit_sha,
        "license": license,
        "topics": topics,
        "languages": languages,
        "discovered_via_niche": discovered_via_niche,
        "clone_path": clone_path,
        "ingested_at": ingested_at,
        "ingestion_status": ingestion_status,
        "extraction_status": extraction_status,
        "notes": notes,
        "manifest_written_at": _now(),
    }

    # Name: owner_name_snapshotID.json
    safe = _safe_name(full_name)
    manifest_path = MANIFESTS_REPOS / f"{safe}_{snapshot_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.debug("Wrote repo manifest: %s", manifest_path)
    return manifest_path


# ---------------------------------------------------------------------------
# Chunk manifest + curated artifact
# ---------------------------------------------------------------------------

def write_chunk_manifest(
    *,
    chunk_id: int,
    repo_id: int,
    snapshot_id: int,
    file_path: str,
    language: str | None,
    chunk_type: str,
    symbol_name: str | None,
    start_line: int,
    end_line: int,
    content_hash: str,
    classification: "Classification",
    provenance_ref: str,
) -> Path:
    """Write a chunk manifest to data/manifests/chunks/."""
    MANIFESTS_CHUNKS.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "chunk_id": chunk_id,
        "repo_id": repo_id,
        "snapshot_id": snapshot_id,
        "file_path": file_path,
        "language": language,
        "chunk_type": chunk_type,
        "symbol_name": symbol_name,
        "start_line": start_line,
        "end_line": end_line,
        "content_hash": content_hash,
        "category_tags": classification.category_tags,
        "primary_tag": classification.primary_tag,
        "longevity_band": classification.longevity_band,
        "longevity_confidence": classification.longevity_confidence,
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
        "provenance_ref": provenance_ref,
        "manifest_written_at": _now(),
    }

    manifest_path = MANIFESTS_CHUNKS / f"chunk_{chunk_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.debug("Wrote chunk manifest: %s", manifest_path)
    return manifest_path


def write_curated_artifact(
    *,
    chunk_id: int,
    repo_id: int,
    snapshot_id: int,
    full_name: str,
    file_path: str,
    chunk: "Chunk",
    classification: "Classification",
) -> Path | None:
    """Write extracted chunk content to data/extracted/<band>/<tag>/."""
    band = classification.longevity_band
    if band not in VALID_BANDS:
        band = "small"

    primary_tag = _safe_name(classification.primary_tag or "general")
    band_dir = EXTRACTED_DIR / band / primary_tag
    band_dir.mkdir(parents=True, exist_ok=True)

    # Build artifact with provenance header + content
    provenance = {
        "chunk_id": chunk_id,
        "repo_id": repo_id,
        "snapshot_id": snapshot_id,
        "repo": full_name,
        "file": file_path,
        "lines": f"{chunk.start_line}-{chunk.end_line}",
        "language": chunk.language,
        "type": chunk.chunk_type,
        "symbol": chunk.symbol_name,
        "tags": classification.category_tags,
        "band": band,
        "confidence": classification.longevity_confidence,
        "hash": chunk.content_hash,
    }

    artifact_lines = [
        "---",
        json.dumps(provenance, indent=2),
        "---",
        "",
        chunk.content,
    ]
    artifact_text = "\n".join(artifact_lines)

    safe_repo = _safe_name(full_name)
    sym = _safe_name(chunk.symbol_name or chunk.chunk_type)
    filename = f"{safe_repo}_{snapshot_id}_{chunk_id}_{sym}.txt"
    artifact_path = band_dir / filename

    try:
        artifact_path.write_text(artifact_text, encoding="utf-8")
        logger.debug("Wrote artifact: %s", artifact_path)
        return artifact_path
    except Exception as exc:
        logger.warning("Failed to write artifact %s: %s", artifact_path, exc)
        return None


# ---------------------------------------------------------------------------
# Teaching artifact sidecar
# ---------------------------------------------------------------------------

def write_teaching_sidecar(artifact: Any) -> Path:
    """Write a teaching artifact JSON to data/teaching/chunk_<id>.json.

    Accepts any object with a ``chunk_id`` attribute and a ``to_dict()`` method
    (i.e. a ``TeachingArtifact``). Duck-typed to avoid circular imports.

    Each file is a self-contained sidecar with provenance references and the
    three deterministic teaching fields. The chunk_id links it back to the
    chunk manifest and the curated artifact without duplicating content.
    """
    TEACHING_DIR.mkdir(parents=True, exist_ok=True)
    path = TEACHING_DIR / f"chunk_{artifact.chunk_id}.json"
    path.write_text(
        json.dumps(artifact.to_dict(), indent=2),
        encoding="utf-8",
    )
    logger.debug("Wrote teaching sidecar: %s", path)
    return path


# ---------------------------------------------------------------------------
# Extraction summary update helper
# ---------------------------------------------------------------------------

def update_repo_manifest_extraction_status(
    full_name: str,
    snapshot_id: int,
    extraction_status: str,
    chunk_count: int,
    file_count: int,
) -> None:
    """Update the extraction_status field in an existing repo manifest."""
    safe = _safe_name(full_name)
    manifest_path = MANIFESTS_REPOS / f"{safe}_{snapshot_id}.json"

    if not manifest_path.exists():
        return

    try:
        data = json.loads(manifest_path.read_text())
        data["extraction_status"] = extraction_status
        data["extracted_chunks"] = chunk_count
        data["inspected_files"] = file_count
        data["extraction_updated_at"] = _now()
        manifest_path.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.warning("Could not update repo manifest extraction status: %s", exc)

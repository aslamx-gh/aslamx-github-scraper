"""Search aggregation service - combines DB and manifest pointer sources."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from .storage import REPOS_SPECIFIC, MULTIPLE_SPECIFIC, get_repo_manifests

logger = logging.getLogger(__name__)


def search_repos(
    conn: sqlite3.Connection, query: str, niche_id: Optional[str] = None
) -> list[dict]:
    """Search repos from DB and manifest pointers.

    Sources:
    - Local repos table
    - Manifest pointers from repos-specific-storage
    - Manifest pointers from multiple-specific-storage

    Deduplicates by full_name.
    Returns results sorted by relevance/stars.
    """
    if not query or len(query) < 2:
        return []

    q_lower = query.lower()
    results = {}  # Use dict to deduplicate by full_name

    # 1. Search local repos table
    rows = conn.execute(
        """SELECT repo_id, full_name, owner, name, source_url, stars FROM repos
           WHERE full_name LIKE ? OR owner LIKE ? OR name LIKE ?
           ORDER BY stars DESC LIMIT 100""",
        (f"%{q_lower}%", f"%{q_lower}%", f"%{q_lower}%"),
    ).fetchall()

    for row in rows:
        full_name = row[1]  # full_name is second column
        if full_name not in results:
            results[full_name] = {
                "full_name": row[1],
                "owner": row[2],
                "name": row[3],
                "source_url": row[4],
                "stars": row[5],
                "source": "local_db",
                "manifest_paths": [],
            }

    # 2. Search manifest pointers
    manifests = get_repo_manifests("all")
    for manifest in manifests:
        full_name = manifest.get("full_name", "")
        # Check if matches query
        if (
            query.lower() in full_name.lower()
            or query.lower() in manifest.get("owner", "").lower()
            or query.lower() in manifest.get("niche_id", "").lower()
        ):
            if full_name not in results:
                # New result from manifest
                results[full_name] = {
                    "full_name": full_name,
                    "owner": full_name.split("/")[0] if "/" in full_name else "",
                    "name": full_name.split("/")[1] if "/" in full_name else "",
                    "source_url": "",
                    "stars": 0,
                    "source": "manifest",
                    "manifest_paths": [],
                }
            # Add manifest path if not in existing result
            results[full_name]["manifest_paths"].append(manifest.get("clone_path", ""))

    # 3. If niche_id specified, also include manifest pointers from that niche
    if niche_id:
        niche_dir = REPOS_SPECIFIC / "niches" / niche_id
        if niche_dir.exists():
            for json_file in niche_dir.glob("*.json"):
                try:
                    import json
                    manifest = json.loads(json_file.read_text())
                    full_name = manifest.get("full_name", "")
                    if full_name:
                        if full_name not in results:
                            results[full_name] = {
                                "full_name": full_name,
                                "owner": full_name.split("/")[0] if "/" in full_name else "",
                                "name": full_name.split("/")[1] if "/" in full_name else "",
                                "source_url": "",
                                "stars": 0,
                                "source": "niche_manifest",
                                "niche_id": niche_id,
                                "manifest_paths": [],
                            }
                        if manifest.get("clone_path"):
                            results[full_name]["manifest_paths"].append(
                                manifest.get("clone_path")
                            )
                except Exception as e:
                    logger.error("Failed to read niche manifest %s: %s", json_file, e)

    # Sort by stars (descending) for results from DB, others by name
    sorted_results = sorted(
        results.values(),
        key=lambda r: (-r.get("stars", 0), r["full_name"]),
    )

    return sorted_results[:50]  # Limit to 50 results


def search_niches_with_repos(
    conn: sqlite3.Connection, query: str
) -> list[dict]:
    """Search niches and optionally expand to show repos.

    Returns:
    - List of niche objects with repo expansion data
    """
    if not query or len(query) < 2:
        return []

    q_lower = query.lower()

    # Search niches table
    rows = conn.execute(
        """SELECT niche_id, title, description, enabled FROM niches
           WHERE title LIKE ? OR description LIKE ? OR niche_id LIKE ?
           ORDER BY title LIMIT 50""",
        (f"%{q_lower}%", f"%{q_lower}%", f"%{q_lower}%"),
    ).fetchall()

    results = []
    for row in rows:
        niche_id = row[0]
        niche_obj = {
            "niche_id": niche_id,
            "title": row[1],
            "description": row[2],
            "enabled": bool(row[3]),
        }

        # Count repos for this niche
        niche_dir = REPOS_SPECIFIC / "niches" / niche_id
        repo_count = 0
        if niche_dir.exists():
            repo_count = len(list(niche_dir.glob("*.json")))

        niche_obj["repo_count"] = repo_count
        results.append(niche_obj)

    return results


def get_niche_repos(conn: sqlite3.Connection, niche_id: str) -> list[dict]:
    """Get all repos associated with a niche.

    Returns repos from:
    - Manifest pointers in repos-specific-storage/niches/{niche_id}/
    - Manifest pointers in multiple-specific-storage/niches/{niche_id}/
    - Deduplicates by full_name
    """
    results = {}

    # Check repos-specific niche dir
    niche_specific_dir = REPOS_SPECIFIC / "niches" / niche_id
    if niche_specific_dir.exists():
        for json_file in niche_specific_dir.glob("*.json"):
            try:
                import json
                manifest = json.loads(json_file.read_text())
                full_name = manifest.get("full_name", "")
                if full_name and full_name not in results:
                    results[full_name] = {
                        "full_name": full_name,
                        "owner": full_name.split("/")[0] if "/" in full_name else "",
                        "name": full_name.split("/")[1] if "/" in full_name else "",
                        "source": "niche_manifest",
                        "niche_id": niche_id,
                        "manifest_paths": [manifest.get("clone_path", "")],
                    }
            except Exception as e:
                logger.error("Failed to read niche manifest %s: %s", json_file, e)

    # Check multiple-specific niche dir
    multiple_niche_dir = MULTIPLE_SPECIFIC / "niches" / niche_id
    if multiple_niche_dir.exists():
        for json_file in multiple_niche_dir.glob("*.json"):
            try:
                import json
                manifest = json.loads(json_file.read_text())
                full_name = manifest.get("full_name", "")
                if full_name:
                    if full_name not in results:
                        results[full_name] = {
                            "full_name": full_name,
                            "owner": full_name.split("/")[0] if "/" in full_name else "",
                            "name": full_name.split("/")[1] if "/" in full_name else "",
                            "source": "multiple_manifest",
                            "niche_id": niche_id,
                            "manifest_paths": [manifest.get("clone_path", "")],
                        }
                    else:
                        # Add additional clone path
                        if manifest.get("clone_path"):
                            results[full_name]["manifest_paths"].append(
                                manifest.get("clone_path")
                            )
            except Exception as e:
                logger.error("Failed to read multiple niche manifest %s: %s", json_file, e)

    # Also check repos table for repos tagged with this niche
    rows = conn.execute(
        "SELECT repo_id, full_name, owner, name, source_url, stars FROM repos LIMIT 100"
    ).fetchall()

    for row in rows:
        full_name = row[1]
        # TODO: This should check if repo is in niche via a junction table if one exists
        # For now, just include manifest-based results

    sorted_results = sorted(results.values(), key=lambda r: r["full_name"])
    return sorted_results

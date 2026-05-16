"""API and UI routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .database import get_connection, serialize_row, serialize_rows
from .github.auth import load_credentials
from .github.client import GitHubClient
from .services import run_service, settings as settings_service
from .services.search_service import search_repos, search_niches_with_repos, get_niche_repos
from .services import group_service
from .services.scheduler import (
    SchedulerService,
    create_schedule,
    delete_schedule,
    get_schedule,
    get_schedules,
    update_schedule,
    validate_cron,
)

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")
ui_router = APIRouter()


# --- Pydantic Models ---

class RunCreate(BaseModel):
    mode: str
    label: str | None = None
    description: str | None = None
    repo_inputs: list[str] | None = None
    niche_ids: list[str] | None = None


class SettingsUpdate(BaseModel):
    settings: dict[str, str]


class NicheUpdate(BaseModel):
    niche_id: str
    enabled: bool


class NicheCreate(BaseModel):
    niche_id: str
    title: str
    description: str = ""
    languages: list[str] = []
    github_search_queries: list[str] = []
    github_topics: list[str] = []
    exclude_terms: list[str] = []
    min_stars: int = 0
    max_repo_size_kb: int = 512000
    min_recent_activity_days: int = 0
    allowed_licenses: list[str] = []
    exclude_forks: bool = True
    enabled: bool = True


class ScheduleCreate(BaseModel):
    name: str
    cron_expression: str
    niche_ids: list[str] = []
    enabled: bool = False


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    niche_ids: list[str] | None = None
    enabled: bool | None = None


class CartAdd(BaseModel):
    full_name: str
    source_url: str = ""
    owner: str = ""
    name: str = ""
    language: str | None = None
    stars: int = 0
    size_kb: int = 0
    license: str | None = None
    topics: list[str] = []
    description: str = ""
    last_pushed_at: str | None = None
    quality_score: float | None = None
    is_fork: bool = False
    is_archived: bool = False


# --- API Routes ---

@api_router.post("/runs")
async def api_create_run(body: RunCreate):
    conn = get_connection()
    try:
        if body.mode not in ("manual_repo_list", "niche_group", "scheduled"):
            raise HTTPException(400, f"Invalid mode: {body.mode}")
        if body.mode == "manual_repo_list" and not body.repo_inputs:
            raise HTTPException(400, "repo_inputs required for manual_repo_list mode")
        if body.mode in ("niche_group", "scheduled") and not body.niche_ids:
            raise HTTPException(400, "niche_ids required for niche_group/scheduled mode")

        run_inputs = {
            "repo_inputs": body.repo_inputs or [],
            "niche_ids": body.niche_ids or [],
        }
        run_id = run_service.create_run(conn, body.mode, body.label, body.description, run_inputs)

        try:
            from .main import thread_pool
            thread_pool.submit(
                run_service.execute_run,
                run_id,
                body.mode,
                body.repo_inputs,
                body.niche_ids,
            )
        except RuntimeError:
            # Thread pool shut down (e.g. during testing) — run stays in searching
            logger.warning("Thread pool unavailable for run %d dispatch", run_id)

        return {"run_id": run_id, "status": "searching", "status_code": 99}
    finally:
        conn.close()


@api_router.get("/runs")
async def api_list_runs():
    conn = get_connection()
    try:
        return run_service.get_runs(conn)
    finally:
        conn.close()


@api_router.get("/runs/{run_id}")
async def api_get_run(run_id: int):
    conn = get_connection()
    try:
        run = run_service.get_run(conn, run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        return run
    finally:
        conn.close()


@api_router.get("/runs/{run_id}/items")
async def api_get_run_items(run_id: int):
    conn = get_connection()
    try:
        return run_service.get_run_items(conn, run_id)
    finally:
        conn.close()


@api_router.get("/runs/{run_id}/failures")
async def api_get_run_failures(run_id: int):
    conn = get_connection()
    try:
        failures = run_service.get_run_failures(conn, run_id)
        rejections = run_service.get_run_rejections(conn, run_id)
        return {"failures": failures, "rejections": rejections}
    finally:
        conn.close()


@api_router.post("/runs/{run_id}/retry")
async def api_retry_run(run_id: int):
    conn = get_connection()
    try:
        run = run_service.get_run(conn, run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        if not run_service.can_retry_run(run["status"]):
            raise HTTPException(400, f"Cannot retry run in status {run['status']}")

        # Recover original inputs from stored run_inputs JSON
        stored_inputs = {}
        if run.get("run_inputs"):
            try:
                stored_inputs = json.loads(run["run_inputs"])
            except (ValueError, TypeError):
                stored_inputs = {}

        repo_inputs = stored_inputs.get("repo_inputs") or None
        niche_ids = stored_inputs.get("niche_ids") or None

        # Fail fast if inputs cannot be recovered (old runs without stored inputs)
        if run["mode"] == "manual_repo_list" and not repo_inputs:
            raise HTTPException(400, "Cannot retry: original repo inputs were not stored. Start a new run.")
        if run["mode"] in ("niche_group", "scheduled") and not niche_ids:
            raise HTTPException(400, "Cannot retry: original niche IDs were not stored. Start a new run.")

        new_run_id = run_service.create_run(
            conn, run["mode"], run.get("label"), run.get("description"),
            run_inputs=stored_inputs,
        )

        from .main import thread_pool
        thread_pool.submit(run_service.execute_run, new_run_id, run["mode"], repo_inputs, niche_ids)

        return {"new_run_id": new_run_id}
    finally:
        conn.close()


@api_router.post("/runs/{run_id}/remove")
async def api_remove_run(run_id: int):
    conn = get_connection()
    try:
        success, err = run_service.remove_run(conn, run_id)
        if not success:
            raise HTTPException(400, err)
        return {"status": "ok"}
    finally:
        conn.close()


@api_router.get("/github/status")
async def api_github_status():
    import asyncio
    credentials = load_credentials()
    client = GitHubClient(credentials)
    loop = asyncio.get_event_loop()
    try:
        from .main import thread_pool
        status = await loop.run_in_executor(thread_pool, client.check_connectivity)
    except RuntimeError:
        # Thread pool shut down (e.g. during testing) — run check directly.
        status = client.check_connectivity()
    return {
        "reachable": status.reachable,
        "authenticated": status.authenticated,
        "auth_mode": status.auth_mode,
        "login": status.login,
        "rate_limit": {
            "limit": status.rate_limit.limit,
            "remaining": status.rate_limit.remaining,
            "reset_at": status.rate_limit.reset_at,
        },
        "error": status.error,
    }


@api_router.get("/settings")
async def api_get_settings():
    conn = get_connection()
    try:
        return settings_service.get_all_settings(conn)
    finally:
        conn.close()


@api_router.put("/settings")
async def api_update_settings(body: SettingsUpdate):
    conn = get_connection()
    try:
        # Validate cron if scheduler settings changed
        if "scheduler.cron" in body.settings:
            valid, err = validate_cron(body.settings["scheduler.cron"])
            if not valid:
                raise HTTPException(400, f"Invalid cron: {err}")

        settings_service.update_settings(conn, body.settings)

        # Restart scheduler if global_enabled changed
        if "scheduler.global_enabled" in body.settings:
            from .main import scheduler_service
            if body.settings["scheduler.global_enabled"] == "true":
                scheduler_service.start()
                scheduler_service.load_schedules(conn)
            else:
                scheduler_service.stop()

        return {"status": "ok"}
    finally:
        conn.close()


@api_router.get("/niches")
async def api_get_niches():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM niches ORDER BY title").fetchall()
        return serialize_rows(rows)
    finally:
        conn.close()


@api_router.post("/niches")
async def api_create_niche(body: NicheCreate):
    conn = get_connection()
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        # Check if niche_id already exists
        existing = conn.execute("SELECT niche_id FROM niches WHERE niche_id=?", (body.niche_id,)).fetchone()
        if existing:
            raise HTTPException(400, f"Niche {body.niche_id} already exists")

        conn.execute(
            """INSERT INTO niches (niche_id, title, description, languages, github_search_queries,
               github_topics, exclude_terms, min_stars, max_repo_size_kb,
               min_recent_activity_days, allowed_licenses, exclude_forks, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                body.niche_id,
                body.title,
                body.description,
                json.dumps(body.languages),
                json.dumps(body.github_search_queries),
                json.dumps(body.github_topics),
                json.dumps(body.exclude_terms),
                body.min_stars,
                body.max_repo_size_kb,
                body.min_recent_activity_days,
                json.dumps(body.allowed_licenses),
                1 if body.exclude_forks else 0,
                1 if body.enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
        return {"niche_id": body.niche_id}
    finally:
        conn.close()


@api_router.put("/niches")
async def api_update_niche(body: NicheUpdate):
    conn = get_connection()
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE niches SET enabled=?, updated_at=? WHERE niche_id=?",
            (1 if body.enabled else 0, now, body.niche_id),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


# --- Group Management Routes ---

class GroupCreate(BaseModel):
    name: str
    description: str = ""


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class GroupItemAdd(BaseModel):
    group_id: int
    full_name: str
    source_url: str = ""
    owner: str = ""
    name: str = ""
    niche_id: str | None = None


@api_router.get("/groups")
async def api_get_groups():
    conn = get_connection()
    try:
        return group_service.get_groups(conn)
    finally:
        conn.close()


@api_router.post("/groups")
async def api_create_group(body: GroupCreate):
    conn = get_connection()
    try:
        group_id = group_service.create_group(conn, body.name, body.description)
        return {"group_id": group_id}
    finally:
        conn.close()


@api_router.get("/groups/{group_id}")
async def api_get_group(group_id: int):
    conn = get_connection()
    try:
        group = group_service.get_group(conn, group_id)
        if not group:
            raise HTTPException(404, "Group not found")
        items = group_service.get_group_items(conn, group_id)
        group["items"] = items
        return group
    finally:
        conn.close()


@api_router.put("/groups/{group_id}")
async def api_update_group(group_id: int, body: GroupUpdate):
    conn = get_connection()
    try:
        success = group_service.update_group(conn, group_id, body.name, body.description)
        if not success:
            raise HTTPException(404, "Group not found")
        return {"status": "ok"}
    finally:
        conn.close()


@api_router.delete("/groups/{group_id}")
async def api_delete_group(group_id: int):
    conn = get_connection()
    try:
        success = group_service.delete_group(conn, group_id)
        if not success:
            raise HTTPException(404, "Group not found")
        return {"status": "ok"}
    finally:
        conn.close()


@api_router.post("/groups/{group_id}/items")
async def api_add_group_item(group_id: int, body: GroupItemAdd):
    conn = get_connection()
    try:
        item_id = group_service.add_item_to_group(
            conn,
            body.group_id if body.group_id else group_id,
            body.full_name,
            body.source_url,
            body.owner,
            body.name,
            body.niche_id,
        )
        if not item_id:
            raise HTTPException(404, "Group not found")
        return {"item_id": item_id}
    finally:
        conn.close()


@api_router.delete("/groups/items/{item_id}")
async def api_remove_group_item(item_id: int):
    conn = get_connection()
    try:
        success = group_service.remove_item_from_group(conn, item_id)
        if not success:
            raise HTTPException(404, "Item not found")
        return {"status": "ok"}
    finally:
        conn.close()


@api_router.post("/groups/{group_id}/clone")
async def api_clone_group(group_id: int):
    conn = get_connection()
    try:
        run_id, error = group_service.clone_group(conn, group_id, run_service)
        if error:
            raise HTTPException(400, error)
        return {"run_id": run_id, "status": "searching", "status_code": 99}
    finally:
        conn.close()


@api_router.get("/schedules")
async def api_get_schedules():
    conn = get_connection()
    try:
        return get_schedules(conn)
    finally:
        conn.close()


@api_router.post("/schedules")
async def api_create_schedule(body: ScheduleCreate):
    conn = get_connection()
    try:
        schedule_id, err = create_schedule(
            conn, body.name, body.cron_expression, body.niche_ids, body.enabled
        )
        if err:
            raise HTTPException(400, err)

        from .main import scheduler_service
        if body.enabled and scheduler_service.is_running:
            scheduler_service.load_schedules(conn)

        return {"schedule_id": schedule_id}
    finally:
        conn.close()


@api_router.put("/schedules/{schedule_id}")
async def api_update_schedule(schedule_id: int, body: ScheduleUpdate):
    conn = get_connection()
    try:
        existing = get_schedule(conn, schedule_id)
        if not existing:
            raise HTTPException(404, "Schedule not found")

        err = update_schedule(
            conn, schedule_id, body.name, body.cron_expression, body.niche_ids, body.enabled
        )
        if err:
            raise HTTPException(400, err)

        from .main import scheduler_service
        if scheduler_service.is_running:
            scheduler_service.load_schedules(conn)

        return {"status": "ok"}
    finally:
        conn.close()


@api_router.delete("/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int):
    conn = get_connection()
    try:
        delete_schedule(conn, schedule_id)

        from .main import scheduler_service
        if scheduler_service.is_running:
            scheduler_service.load_schedules(conn)

        return {"status": "ok"}
    finally:
        conn.close()


@api_router.get("/search/repos")
async def api_search_repos(q: str = "", niche_id: str = ""):
    conn = get_connection()
    try:
        results = search_repos(conn, q, niche_id if niche_id else None)
        return results
    finally:
        conn.close()


@api_router.get("/search/niches")
async def api_search_niches(q: str = ""):
    conn = get_connection()
    try:
        results = search_niches_with_repos(conn, q)
        return results
    finally:
        conn.close()


@api_router.get("/search/niches/{niche_id}/repos")
async def api_get_niche_repos(niche_id: str):
    """Get all repos associated with a niche."""
    conn = get_connection()
    try:
        results = get_niche_repos(conn, niche_id)
        return results
    finally:
        conn.close()


@api_router.get("/repos/{repo_id}/extraction")
async def api_get_repo_extraction(repo_id: int):
    """Get extraction status and chunk counts for a repo."""
    conn = get_connection()
    try:
        repo = conn.execute("SELECT * FROM repos WHERE repo_id=?", (repo_id,)).fetchone()
        if not repo:
            raise HTTPException(404, "Repo not found")

        snapshot = conn.execute(
            "SELECT * FROM repo_snapshots WHERE repo_id=? ORDER BY snapshot_at DESC LIMIT 1",
            (repo_id,),
        ).fetchone()

        result: dict[str, Any] = {
            "repo_id": repo_id,
            "full_name": repo["full_name"],
            "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
            "extraction_status": snapshot["extraction_status"] if snapshot else "no_snapshot",
        }

        if snapshot:
            sid = snapshot["snapshot_id"]
            chunk_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM chunks WHERE snapshot_id=?", (sid,)
            ).fetchone()["cnt"]
            file_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=?", (sid,)
            ).fetchone()["cnt"]
            included_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=? AND included=1", (sid,)
            ).fetchone()["cnt"]
            band_counts = conn.execute(
                """SELECT longevity_band, COUNT(*) as cnt FROM chunks
                   WHERE snapshot_id=? GROUP BY longevity_band""",
                (sid,),
            ).fetchall()
            result["chunk_count"] = chunk_count
            result["file_count"] = file_count
            result["included_file_count"] = included_count
            result["band_counts"] = {row["longevity_band"]: row["cnt"] for row in band_counts}

        return result
    finally:
        conn.close()


@api_router.get("/extraction/summary")
async def api_extraction_summary():
    """Get aggregate extraction stats across all repos."""
    conn = get_connection()
    try:
        total_chunks = conn.execute("SELECT COUNT(*) as cnt FROM chunks").fetchone()["cnt"]
        total_files = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE included=1"
        ).fetchone()["cnt"]
        band_counts = conn.execute(
            "SELECT longevity_band, COUNT(*) as cnt FROM chunks GROUP BY longevity_band"
        ).fetchall()
        repos_extracted = conn.execute(
            "SELECT COUNT(DISTINCT repo_id) as cnt FROM chunks"
        ).fetchone()["cnt"]
        status_counts = conn.execute(
            "SELECT extraction_status, COUNT(*) as cnt FROM repo_snapshots GROUP BY extraction_status"
        ).fetchall()
        return {
            "total_chunks": total_chunks,
            "total_included_files": total_files,
            "repos_with_chunks": repos_extracted,
            "band_counts": {row["longevity_band"]: row["cnt"] for row in band_counts},
            "snapshot_status_counts": {row["extraction_status"]: row["cnt"] for row in status_counts},
        }
    finally:
        conn.close()


@api_router.get("/validation/summary")
async def api_validation_summary():
    """Aggregate validation stats: counts by status and quarantine reason."""
    from .services.validation import get_validation_summary
    return get_validation_summary()


@api_router.post("/validation/run")
async def api_validation_run():
    """
    Trigger validation for all chunks with validation_status='pending'.
    Runs in a background thread. Returns pending count immediately.
    """
    conn = get_connection()
    try:
        pending_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM chunks WHERE validation_status = 'pending'"
        ).fetchone()["cnt"]
    finally:
        conn.close()

    if pending_count == 0:
        return {"status": "nothing_to_do", "pending_chunks": 0}

    try:
        from .main import thread_pool
        from .services.validation import validate_pending_chunks
        thread_pool.submit(validate_pending_chunks)
    except RuntimeError:
        logger.warning("Thread pool unavailable for validation dispatch")

    return {"status": "queued", "pending_chunks": pending_count}


@api_router.post("/extraction/backfill")
async def api_extraction_backfill():
    """
    Queue extraction for all snapshots with extraction_status='pending'.
    Runs in a background thread. Returns the pending count immediately.
    """
    conn = get_connection()
    try:
        pending_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM repo_snapshots WHERE extraction_status='pending'"
        ).fetchone()["cnt"]
    finally:
        conn.close()

    if pending_count == 0:
        return {"status": "nothing_to_do", "pending_snapshots": 0}

    try:
        from .main import thread_pool
        thread_pool.submit(run_service.backfill_pending_extraction)
    except RuntimeError:
        # Thread pool unavailable (e.g. during testing)
        logger.warning("Thread pool unavailable for backfill dispatch")

    return {"status": "queued", "pending_snapshots": pending_count}


@api_router.post("/repos/{repo_id}/extraction/run")
async def api_run_repo_extraction(repo_id: int):
    """
    Queue extraction rerun for a single repo's latest snapshot.
    Runs in a background thread.
    """
    conn = get_connection()
    try:
        repo = conn.execute("SELECT full_name FROM repos WHERE repo_id=?", (repo_id,)).fetchone()
        if not repo:
            raise HTTPException(404, "Repo not found")
        snapshot = conn.execute(
            "SELECT snapshot_id FROM repo_snapshots WHERE repo_id=? ORDER BY snapshot_id DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        if not snapshot:
            raise HTTPException(404, "No snapshot found for this repo")
    finally:
        conn.close()

    try:
        from .main import thread_pool
        thread_pool.submit(run_service.run_repo_extraction, repo_id)
    except RuntimeError:
        logger.warning("Thread pool unavailable for per-repo extraction dispatch")

    return {"status": "queued", "repo_id": repo_id, "full_name": repo["full_name"]}


# --- Discovery Route ---

@api_router.get("/discover")
async def api_discover(q: str = "", language: str = "", min_stars: int = 0):
    """Live GitHub search with quality scoring.

    Applies local filter settings to flag candidates.  Does not clone anything.
    Results are sorted by quality_score descending so the best candidates appear first.
    """
    import asyncio
    if not q or len(q.strip()) < 2:
        return []

    query = q.strip()
    if language:
        query = f"{query} language:{language}"
    if min_stars > 0:
        query = f"{query} stars:>={min_stars}"

    credentials = load_credentials()
    client = GitHubClient(credentials)

    loop = asyncio.get_event_loop()
    try:
        from .main import thread_pool
        repos = await loop.run_in_executor(thread_pool, lambda: client.search_repos(query))
    except RuntimeError:
        repos = client.search_repos(query)

    from .services.discovery import _attach_quality_signals
    for repo in repos:
        if "_quality_score" not in repo:
            _attach_quality_signals(repo)

    conn = get_connection()
    try:
        from .services.filtering import get_filter_settings, filter_candidate
        settings = get_filter_settings(conn)
    finally:
        conn.close()

    results = []
    for repo in repos:
        accepted, reason_code, explanation = filter_candidate(repo, None, settings)
        results.append({
            **repo,
            "quality_score": repo.get("_quality_score"),
            "filter_accepted": accepted,
            "filter_reason": reason_code,
        })

    results.sort(key=lambda r: (r.get("quality_score") or 0), reverse=True)
    return results


# --- Cart Routes ---

@api_router.get("/cart")
async def api_get_cart():
    conn = get_connection()
    try:
        from .services.cart_service import get_cart
        return get_cart(conn)
    finally:
        conn.close()


@api_router.get("/cart/count")
async def api_get_cart_count():
    conn = get_connection()
    try:
        from .services.cart_service import get_cart_count
        return {"count": get_cart_count(conn)}
    finally:
        conn.close()


@api_router.post("/cart")
async def api_add_to_cart(body: CartAdd):
    conn = get_connection()
    try:
        from .services.cart_service import add_to_cart
        item_id, existed = add_to_cart(conn, body.model_dump())
        return {"item_id": item_id, "already_in_cart": existed}
    finally:
        conn.close()


@api_router.delete("/cart/{item_id}")
async def api_remove_from_cart(item_id: int):
    conn = get_connection()
    try:
        from .services.cart_service import remove_from_cart
        if not remove_from_cart(conn, item_id):
            raise HTTPException(404, "Item not found in cart")
        return {"status": "ok"}
    finally:
        conn.close()


@api_router.delete("/cart")
async def api_clear_cart():
    conn = get_connection()
    try:
        from .services.cart_service import clear_cart
        count = clear_cart(conn)
        return {"cleared": count}
    finally:
        conn.close()


@api_router.post("/cart/clone")
async def api_clone_from_cart():
    conn = get_connection()
    try:
        from .services.cart_service import get_cart
        items = get_cart(conn)
        if not items:
            raise HTTPException(400, "Cart is empty — add repos before cloning")

        repo_inputs = [item["full_name"] for item in items]
        run_inputs = {"repo_inputs": repo_inputs, "niche_ids": []}
        run_id = run_service.create_run(
            conn,
            mode="manual_repo_list",
            label=f"Cart clone ({len(repo_inputs)} repos)",
            description="Cloned from shortlist cart",
            run_inputs=run_inputs,
        )
    finally:
        conn.close()

    try:
        from .main import thread_pool
        thread_pool.submit(run_service.execute_run, run_id, "manual_repo_list", repo_inputs, None)
    except RuntimeError:
        logger.warning("Thread pool unavailable for cart clone dispatch")

    return {"run_id": run_id, "status": "searching", "repo_count": len(repo_inputs)}


# --- UI Routes ---

def _get_templates():
    from .main import templates
    return templates


@ui_router.get("/", response_class=HTMLResponse)
async def ui_dashboard(request: Request):
    conn = get_connection()
    try:
        current_run, recent_runs = run_service.get_current_and_recent_runs(conn, limit=20)
        from .services.cart_service import get_cart_count
        cart_count = get_cart_count(conn)
        return _get_templates().TemplateResponse("dashboard.html", {
            "request": request,
            "current_run": current_run,
            "recent_runs": recent_runs,
            "cart_count": cart_count,
            "page": "dashboard",
        })
    finally:
        conn.close()


@ui_router.get("/cart", response_class=HTMLResponse)
async def ui_cart(request: Request):
    conn = get_connection()
    try:
        from .services.cart_service import get_cart
        items = get_cart(conn)
        return _get_templates().TemplateResponse("cart.html", {
            "request": request,
            "items": items,
            "count": len(items),
            "page": "cart",
        })
    finally:
        conn.close()


@ui_router.get("/runs/{run_id}", response_class=HTMLResponse)
async def ui_run_detail(request: Request, run_id: int):
    from datetime import datetime, timezone
    conn = get_connection()
    try:
        run = run_service.get_run(conn, run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        items = run_service.get_run_items(conn, run_id)
        failures = run_service.get_run_failures(conn, run_id)
        rejections = run_service.get_run_rejections(conn, run_id)

        # Compute elapsed time
        elapsed_seconds = None
        if run.get("started_at"):
            try:
                start = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
                if run.get("finished_at"):
                    end = datetime.fromisoformat(run["finished_at"].replace("Z", "+00:00"))
                else:
                    end = datetime.now(timezone.utc)
                elapsed_seconds = int((end - start).total_seconds())
            except (ValueError, TypeError):
                pass

        # Build clone path lookup for succeeded items (item_id -> clone_path)
        item_clone_paths: dict[int, str] = {}
        # Build extraction stats lookup (snapshot_id -> {chunks, extraction_status})
        item_extraction: dict[int, dict] = {}
        for item in items:
            repo_id = item.get("repo_id")
            if repo_id and item.get("status") == "succeed":
                row = conn.execute(
                    "SELECT clone_path FROM repos WHERE repo_id=?", (repo_id,)
                ).fetchone()
                if row and row["clone_path"]:
                    item_clone_paths[item["item_id"]] = row["clone_path"]

            snapshot_id = item.get("snapshot_id")
            if snapshot_id:
                snap = conn.execute(
                    "SELECT extraction_status FROM repo_snapshots WHERE snapshot_id=?",
                    (snapshot_id,),
                ).fetchone()
                chunk_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM chunks WHERE snapshot_id=?", (snapshot_id,)
                ).fetchone()["cnt"]
                item_extraction[item["item_id"]] = {
                    "extraction_status": snap["extraction_status"] if snap else "unknown",
                    "chunk_count": chunk_count,
                }

        return _get_templates().TemplateResponse("run_detail.html", {
            "request": request,
            "run": run,
            "items": items,
            "failures": failures,
            "rejections": rejections,
            "elapsed_seconds": elapsed_seconds,
            "item_clone_paths": item_clone_paths,
            "item_extraction": item_extraction,
            "page": "runs",
        })
    finally:
        conn.close()


@ui_router.get("/extraction", response_class=HTMLResponse)
async def ui_extraction(request: Request):
    conn = get_connection()
    try:
        # Summary stats
        total_chunks = conn.execute("SELECT COUNT(*) as cnt FROM chunks").fetchone()["cnt"]
        total_files = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE included=1").fetchone()["cnt"]
        band_rows = conn.execute(
            "SELECT longevity_band, COUNT(*) as cnt FROM chunks GROUP BY longevity_band"
        ).fetchall()
        repos_with_chunks = conn.execute(
            "SELECT COUNT(DISTINCT repo_id) as cnt FROM chunks"
        ).fetchone()["cnt"]
        status_rows = conn.execute(
            "SELECT extraction_status, COUNT(*) as cnt FROM repo_snapshots GROUP BY extraction_status"
        ).fetchall()
        snapshot_status_counts = {r["extraction_status"]: r["cnt"] for r in status_rows}
        summary = {
            "total_chunks": total_chunks,
            "total_included_files": total_files,
            "repos_with_chunks": repos_with_chunks,
            "band_counts": {r["longevity_band"]: r["cnt"] for r in band_rows},
        }

        # Per-repo extraction table
        repo_rows = conn.execute(
            """SELECT r.repo_id, r.full_name,
                      s.snapshot_id, s.extraction_status
               FROM repos r
               JOIN repo_snapshots s ON s.repo_id = r.repo_id
               WHERE s.snapshot_id = (
                   SELECT MAX(s2.snapshot_id) FROM repo_snapshots s2 WHERE s2.repo_id = r.repo_id
               )
               ORDER BY r.full_name"""
        ).fetchall()

        repos = []
        for row in repo_rows:
            sid = row["snapshot_id"]
            chunk_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM chunks WHERE snapshot_id=?", (sid,)
            ).fetchone()["cnt"]
            file_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=?", (sid,)
            ).fetchone()["cnt"]
            included_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=? AND included=1", (sid,)
            ).fetchone()["cnt"]
            band_counts = conn.execute(
                "SELECT longevity_band, COUNT(*) as cnt FROM chunks WHERE snapshot_id=? GROUP BY longevity_band",
                (sid,),
            ).fetchall()
            repos.append({
                "repo_id": row["repo_id"],
                "full_name": row["full_name"],
                "snapshot_id": sid,
                "extraction_status": row["extraction_status"],
                "chunk_count": chunk_count,
                "file_count": file_count,
                "included_file_count": included_count,
                "bands": {r["longevity_band"]: r["cnt"] for r in band_counts},
            })

        return _get_templates().TemplateResponse("extraction.html", {
            "request": request,
            "summary": summary,
            "repos": repos,
            "snapshot_status_counts": snapshot_status_counts,
            "page": "extraction",
        })
    finally:
        conn.close()


@ui_router.get("/extraction/{repo_id}", response_class=HTMLResponse)
async def ui_extraction_repo(request: Request, repo_id: int):
    conn = get_connection()
    try:
        repo = conn.execute("SELECT * FROM repos WHERE repo_id=?", (repo_id,)).fetchone()
        if not repo:
            raise HTTPException(404, "Repo not found")

        snapshot = conn.execute(
            "SELECT * FROM repo_snapshots WHERE repo_id=? ORDER BY snapshot_at DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        if not snapshot:
            raise HTTPException(404, "No snapshot found")

        sid = snapshot["snapshot_id"]

        # Chunks with their tags
        chunk_rows = conn.execute(
            "SELECT * FROM chunks WHERE snapshot_id=? ORDER BY file_id, start_line",
            (sid,),
        ).fetchall()

        # Build tag list per chunk
        chunks = []
        all_tags: set = set()
        for c in chunk_rows:
            import json
            tags = [r["tag"] for r in conn.execute(
                "SELECT tag FROM chunk_tags WHERE chunk_id=?", (c["chunk_id"],)
            ).fetchall()]
            all_tags.update(tags)
            chunks.append({
                "chunk_id": c["chunk_id"],
                "chunk_type": c["chunk_type"],
                "symbol_name": c["symbol_name"],
                "file_path": _get_file_path(conn, c["file_id"]),
                "start_line": c["start_line"],
                "end_line": c["end_line"],
                "language": c["language"],
                "longevity_band": c["longevity_band"],
                "longevity_confidence": c["longevity_confidence"],
                "primary_tag": c["primary_tag"],
                "tags": tags,
            })

        file_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=?", (sid,)
        ).fetchone()["cnt"]
        included_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM files WHERE snapshot_id=? AND included=1", (sid,)
        ).fetchone()["cnt"]

        skipped = conn.execute(
            "SELECT relative_path, skip_reason FROM files WHERE snapshot_id=? AND included=0 ORDER BY skip_reason, relative_path LIMIT 200",
            (sid,),
        ).fetchall()

        return _get_templates().TemplateResponse("extraction_repo.html", {
            "request": request,
            "repo": dict(repo),
            "snapshot": dict(snapshot),
            "chunks": chunks,
            "all_tags": sorted(all_tags),
            "file_count": file_count,
            "included_count": included_count,
            "skipped_files": [dict(r) for r in skipped],
            "page": "extraction",
        })
    finally:
        conn.close()


@ui_router.get("/validation", response_class=HTMLResponse)
async def ui_validation(request: Request):
    from .services.validation import get_validation_summary
    summary = get_validation_summary()
    return _get_templates().TemplateResponse("validation.html", {
        "request": request,
        "summary": summary,
        "page": "validation",
    })


def _get_file_path(conn, file_id: int) -> str:
    row = conn.execute("SELECT relative_path FROM files WHERE file_id=?", (file_id,)).fetchone()
    return row["relative_path"] if row else "unknown"


@ui_router.get("/failures", response_class=HTMLResponse)
async def ui_failures(request: Request):
    conn = get_connection()
    try:
        failures = run_service.get_all_failures(conn)
        rejections = run_service.get_all_rejections(conn)
        return _get_templates().TemplateResponse("failures.html", {
            "request": request,
            "failures": failures,
            "rejections": rejections,
            "page": "failures",
        })
    finally:
        conn.close()


@ui_router.get("/settings", response_class=HTMLResponse)
async def ui_settings(request: Request):
    conn = get_connection()
    try:
        all_settings = settings_service.get_all_settings(conn)
        return _get_templates().TemplateResponse("settings.html", {
            "request": request,
            "settings": all_settings,
            "page": "settings",
        })
    finally:
        conn.close()


@ui_router.get("/niches", response_class=HTMLResponse)
async def ui_niches(request: Request):
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM niches ORDER BY title").fetchall()
        niches = serialize_rows(rows)
        return _get_templates().TemplateResponse("niches.html", {
            "request": request,
            "niches": niches,
            "page": "niches",
        })
    finally:
        conn.close()


@ui_router.get("/schedules", response_class=HTMLResponse)
async def ui_schedules(request: Request):
    conn = get_connection()
    try:
        scheds = get_schedules(conn)
        niches_rows = conn.execute("SELECT niche_id, title FROM niches WHERE enabled=1 ORDER BY title").fetchall()
        niches = serialize_rows(niches_rows)
        from .main import scheduler_service
        return _get_templates().TemplateResponse("schedules.html", {
            "request": request,
            "schedules": scheds,
            "niches": niches,
            "scheduler_running": scheduler_service.is_running,
            "page": "schedules",
        })
    finally:
        conn.close()


@ui_router.get("/github", response_class=HTMLResponse)
async def ui_github(request: Request):
    return _get_templates().TemplateResponse("github.html", {
        "request": request,
        "page": "github",
    })


@ui_router.get("/search", response_class=HTMLResponse)
async def ui_search(request: Request):
    return _get_templates().TemplateResponse("search.html", {
        "request": request,
        "page": "search",
    })


# HTMX partial endpoints

@ui_router.get("/partials/run-status/{run_id}", response_class=HTMLResponse)
async def partial_run_status(request: Request, run_id: int):
    conn = get_connection()
    try:
        run = run_service.get_run(conn, run_id)
        items = run_service.get_run_items(conn, run_id) if run else []
        return _get_templates().TemplateResponse("partials/run_status.html", {
            "request": request,
            "run": run,
            "items": items,
        })
    finally:
        conn.close()


@ui_router.get("/partials/dashboard-runs", response_class=HTMLResponse)
async def partial_dashboard_runs(request: Request):
    conn = get_connection()
    try:
        current_run, recent_runs = run_service.get_current_and_recent_runs(conn, limit=20)
        return _get_templates().TemplateResponse("partials/dashboard_runs.html", {
            "request": request,
            "current_run": current_run,
            "recent_runs": recent_runs,
        })
    finally:
        conn.close()

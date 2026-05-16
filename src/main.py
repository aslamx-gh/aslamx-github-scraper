"""FastAPI application entry point."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config_loader import load_all_niches
from .database import get_connection, init_db, migrate_db, seed_defaults, upsert_niches, DATA_DIR
from .services.scheduler import SchedulerService
from .services.settings import get_setting

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Shared resources
thread_pool = ThreadPoolExecutor(max_workers=2)
scheduler_service = SchedulerService()


def ensure_dirs() -> None:
    for d in [
        DATA_DIR / "repos",
        DATA_DIR / "manifests" / "repos",
        DATA_DIR / "manifests" / "chunks",
        DATA_DIR / "extracted" / "small",
        DATA_DIR / "extracted" / "medium",
        DATA_DIR / "extracted" / "high",
        DATA_DIR / "extracted" / "very-high",
        DATA_DIR / "runs",
        DATA_DIR / "indexes",
        DATA_DIR / "quarantine",
        DATA_DIR / "teaching",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # Ensure organized storage directories
    from .services.storage import ensure_storage_dirs
    ensure_storage_dirs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    ensure_dirs()
    conn = get_connection()
    init_db(conn)
    migrate_db(conn)
    seed_defaults(conn)
    niches = load_all_niches()
    if niches:
        upsert_niches(conn, niches)
        logger.info("Loaded %d niches from config", len(niches))

    global_enabled = get_setting(conn, "scheduler.global_enabled")
    if global_enabled == "true":
        scheduler_service.start()
        scheduler_service.load_schedules(conn)
        logger.info("Scheduler started with enabled schedules")

    conn.close()
    logger.info("Application started — http://localhost:8000")

    yield

    # Shutdown
    scheduler_service.stop()
    thread_pool.shutdown(wait=False)
    logger.info("Application stopped")


app = FastAPI(title="GitHub Cloner V1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Import and include routes
from .routes import api_router, ui_router  # noqa: E402

app.include_router(api_router)
app.include_router(ui_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)

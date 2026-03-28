from contextlib import asynccontextmanager
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.deps import get_snapshot_buffer
from app.services.minute_processor import process_all_open_sessions


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    buffer = get_snapshot_buffer()
    scheduler = AsyncIOScheduler(timezone="UTC")

    try:
        await init_db()
        logger.info("Database initialization completed")
    except Exception:
        logger.exception("Database initialization failed")
        raise

    async def tick() -> None:
        async with SessionLocal() as session:
            try:
                await process_all_open_sessions(session, buffer, settings)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Scheduled minute rollup failed")

    scheduler.add_job(tick, "interval", seconds=settings.dashboard_bucket_seconds, id="minute_rollup")
    scheduler.start()
    logger.info("Scheduler started: minute_rollup every %s seconds", settings.dashboard_bucket_seconds)
    yield
    try:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown completed")
    except Exception:
        logger.exception("Scheduler shutdown failed")


app = FastAPI(title="Cognitive State API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DASH_DIR = Path(__file__).resolve().parent.parent / "static" / "dashboard"
if _DASH_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_DASH_DIR), html=True), name="dashboard_ui")


@app.middleware("http")
async def log_unhandled_request_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled request error: method=%s path=%s", request.method, request.url.path)
        raise


app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

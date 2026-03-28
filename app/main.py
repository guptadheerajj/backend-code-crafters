from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis
from fastapi import FastAPI

from app.config import settings
from app.dependencies import create_redis
from app.routers import dashboard, feedback, sessions, snapshots, users

_redis_singleton: redis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _redis_singleton
    _redis_singleton = create_redis()
    app.state.redis = _redis_singleton
    yield
    await _redis_singleton.aclose()
    _redis_singleton = None


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(sessions.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(snapshots.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

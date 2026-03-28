from fastapi import APIRouter

from app.api.v1 import dashboard, sessions

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(sessions.router, tags=["sessions"])
api_router.include_router(dashboard.router, tags=["dashboard"])

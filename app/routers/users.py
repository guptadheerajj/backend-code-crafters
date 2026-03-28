import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.dependencies import DbDep
from app.models import SessionRecord
from app.schemas.session import SessionOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}/sessions", response_model=list[SessionOut])
async def list_user_sessions(
    user_id: uuid.UUID,
    db: DbDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[SessionRecord]:
    r = await db.execute(
        select(SessionRecord)
        .where(SessionRecord.user_id == user_id)
        .order_by(SessionRecord.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(r.scalars().all())

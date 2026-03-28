import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.dependencies import DbDep
from app.models import SessionRecord
from app.schemas.session import SessionCreate, SessionEnd, SessionOut

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut)
async def start_session(body: SessionCreate, db: DbDep) -> SessionRecord:
    if body.external_ref:
        r = await db.execute(select(SessionRecord).where(SessionRecord.external_ref == body.external_ref))
        existing = r.scalar_one_or_none()
        if existing:
            return existing

    sess = SessionRecord(
        user_id=body.user_id,
        device_id=body.device_id,
        external_ref=body.external_ref,
        meta=body.meta,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: uuid.UUID, db: DbDep) -> SessionRecord:
    sess = await db.get(SessionRecord, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@router.post("/{session_id}/end", response_model=SessionOut)
async def end_session(session_id: uuid.UUID, body: SessionEnd, db: DbDep) -> SessionRecord:
    sess = await db.get(SessionRecord, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if sess.status != "active":
        return sess

    if body.status not in ("ended", "abandoned"):
        raise HTTPException(status_code=400, detail="status must be ended or abandoned")

    sess.status = body.status
    sess.ended_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sess)
    return sess

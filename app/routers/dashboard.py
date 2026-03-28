import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import DbDep
from app.models import CognitiveStatePrediction, SessionRecord, Snapshot
from app.models.summary import SessionSummary
from app.schemas.dashboard import (
    TimelineItem,
    TimelineItemType,
    TimelinePage,
    TimelinePredictionData,
    TimelineSnapshotData,
)
from app.schemas.prediction import PredictionOut
from app.schemas.summary import SessionSummaryOut


router = APIRouter(tags=["dashboard"])


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    keyboard = payload.get("keyboard") if isinstance(payload.get("keyboard"), dict) else {}
    camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else {}
    derived = payload.get("derived") if isinstance(payload.get("derived"), dict) else {}
    return {
        "wpm": keyboard.get("wpm"),
        "hr_bpm": camera.get("hr_bpm"),
        "stress_index": derived.get("stress_index"),
        "fatigue_index": derived.get("fatigue_index"),
    }


@router.get("/sessions/{session_id}/summary", response_model=SessionSummaryOut)
async def get_session_summary(session_id: uuid.UUID, db: DbDep) -> SessionSummary:
    r = await db.execute(select(SessionSummary).where(SessionSummary.session_id == session_id))
    row = r.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found")
    return row


@router.get("/sessions/{session_id}/predictions", response_model=list[PredictionOut])
async def list_predictions(
    session_id: uuid.UUID,
    db: DbDep,
    limit: int = Query(50, ge=1, le=200),
) -> list[CognitiveStatePrediction]:
    await _require_session(db, session_id)
    r = await db.execute(
        select(CognitiveStatePrediction)
        .where(CognitiveStatePrediction.session_id == session_id)
        .order_by(CognitiveStatePrediction.computed_at.desc())
        .limit(limit)
    )
    return list(r.scalars().all())


@router.get("/sessions/{session_id}/timeline", response_model=TimelinePage)
async def session_timeline(
    session_id: uuid.UUID,
    db: DbDep,
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = None,
) -> TimelinePage:
    await _require_session(db, session_id)

    snap_stmt: Select = (
        select(
            Snapshot.id.label("sid"),
            Snapshot.captured_at.label("at"),
            Snapshot.client_seq,
            Snapshot.payload,
        )
        .where(Snapshot.session_id == session_id)
        .order_by(Snapshot.captured_at.desc())
        .limit(limit * 2)
    )
    if before is not None:
        snap_stmt = snap_stmt.where(Snapshot.captured_at < before)

    pred_stmt: Select = (
        select(
            CognitiveStatePrediction.id.label("pid"),
            CognitiveStatePrediction.computed_at.label("at"),
            CognitiveStatePrediction.state_label,
            CognitiveStatePrediction.state_scores,
        )
        .where(CognitiveStatePrediction.session_id == session_id)
        .order_by(CognitiveStatePrediction.computed_at.desc())
        .limit(limit * 2)
    )
    if before is not None:
        pred_stmt = pred_stmt.where(CognitiveStatePrediction.computed_at < before)

    snaps = (await db.execute(snap_stmt)).mappings().all()
    preds = (await db.execute(pred_stmt)).mappings().all()

    items: list[TimelineItem] = []
    for s in snaps:
        items.append(
            TimelineItem(
                type=TimelineItemType.snapshot,
                at=s["at"],
                data=TimelineSnapshotData(
                    snapshot_id=int(s["sid"]),
                    client_seq=s["client_seq"],
                    captured_at=s["at"],
                    payload_summary=_payload_summary(s["payload"] or {}),
                ),
            )
        )
    for p in preds:
        items.append(
            TimelineItem(
                type=TimelineItemType.prediction,
                at=p["at"],
                data=TimelinePredictionData(
                    prediction_id=int(p["pid"]),
                    state_label=p["state_label"],
                    state_scores=p["state_scores"] or {},
                    computed_at=p["at"],
                ),
            )
        )

    items.sort(key=lambda x: x.at, reverse=True)
    items = items[:limit]
    next_cursor = None
    if items and len(items) == limit:
        next_cursor = items[-1].at.isoformat()

    return TimelinePage(session_id=session_id, items=items, next_cursor=next_cursor)


async def _require_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    sess = await db.get(SessionRecord, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

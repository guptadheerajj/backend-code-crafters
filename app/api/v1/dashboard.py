from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DashboardMetricRow, SessionRow, get_db

router = APIRouter()


@router.get("/dashboard/{session_id}")
async def get_dashboard(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=120, ge=1, le=5000),
) -> dict:
    r = await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    session_row = r.scalar_one_or_none()
    if session_row is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    q = select(DashboardMetricRow).where(DashboardMetricRow.session_id == session_id)
    if from_ts is not None:
        q = q.where(DashboardMetricRow.bucket_start >= from_ts)
    if to_ts is not None:
        q = q.where(DashboardMetricRow.bucket_start <= to_ts)
    q = q.order_by(DashboardMetricRow.bucket_start.desc()).limit(limit)

    res = await db.execute(q)
    rows = res.scalars().all()

    return {
        "session_id": str(session_id),
        "session": {
            "started_at": session_row.started_at.isoformat() if session_row.started_at else None,
            "ended_at": session_row.ended_at.isoformat() if session_row.ended_at else None,
            "external_user_id": session_row.external_user_id,
        },
        "points": [
            {
                "timestamp": m.bucket_start.isoformat(),
                "avg_heart_rate": m.avg_heart_rate,
                "avg_hrv": m.avg_hrv,
                "avg_spo2": m.avg_spo2,
                "stress_index": m.stress_index,
                "fatigue_index": m.fatigue_index,
                "focus_percentage": m.focus_percentage,
                "fatigue_percentage": m.fatigue_percentage,
                "confusion_percentage": m.confusion_percentage,
                "productivity_score": m.productivity_score,
                "state_label": m.state_label,
                "model_version": m.model_version,
            }
            for m in reversed(rows)
        ],
    }

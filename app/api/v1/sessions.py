from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import DashboardMetricRow, SessionRow, get_db
from app.deps import get_snapshot_buffer
from app.schemas.snapshot import SessionCreateIn, SnapshotPayloadIn
from app.services.buffer import SnapshotBuffer, floor_to_minute_utc
from app.services.minute_processor import process_minute_for_session
from app.services.normalize import normalize_snapshot

router = APIRouter()


@router.get("/sessions")
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    external_user_id: str | None = Query(default=None, description="Filter by extension / user id"),
) -> dict:
    q = select(SessionRow).order_by(SessionRow.started_at.desc()).limit(limit)
    if external_user_id:
        q = q.where(SessionRow.external_user_id == external_user_id)
    res = await db.execute(q)
    rows = res.scalars().all()
    if not rows:
        return {"sessions": []}

    ids = [r.id for r in rows]
    agg_res = await db.execute(
        select(
            DashboardMetricRow.session_id,
            func.count().label("bucket_count"),
            func.max(DashboardMetricRow.bucket_start).label("last_bucket_at"),
            func.avg(DashboardMetricRow.focus_percentage).label("avg_focus_pct"),
            func.avg(DashboardMetricRow.fatigue_percentage).label("avg_fatigue_pct"),
            func.avg(DashboardMetricRow.confusion_percentage).label("avg_confusion_pct"),
            func.avg(DashboardMetricRow.productivity_score).label("avg_productivity"),
            func.avg(DashboardMetricRow.avg_heart_rate).label("avg_hr"),
            func.avg(DashboardMetricRow.avg_hrv).label("avg_hrv"),
            func.avg(DashboardMetricRow.avg_spo2).label("avg_spo2"),
        )
        .where(DashboardMetricRow.session_id.in_(ids))
        .group_by(DashboardMetricRow.session_id)
    )
    agg: dict[UUID, dict] = {}
    for row in agg_res.all():
        agg[row.session_id] = {
            "bucket_count": int(row.bucket_count or 0),
            "last_bucket_at": row.last_bucket_at.isoformat() if row.last_bucket_at else None,
            "avg_focus_pct": float(row.avg_focus_pct) if row.avg_focus_pct is not None else None,
            "avg_fatigue_pct": float(row.avg_fatigue_pct) if row.avg_fatigue_pct is not None else None,
            "avg_confusion_pct": float(row.avg_confusion_pct) if row.avg_confusion_pct is not None else None,
            "avg_productivity": float(row.avg_productivity) if row.avg_productivity is not None else None,
            "avg_hr": float(row.avg_hr) if row.avg_hr is not None else None,
            "avg_hrv": float(row.avg_hrv) if row.avg_hrv is not None else None,
            "avg_spo2": float(row.avg_spo2) if row.avg_spo2 is not None else None,
        }

    out = []
    for s in rows:
        m = agg.get(s.id)
        out.append(
            {
                "id": str(s.id),
                "external_user_id": s.external_user_id,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "metrics_summary": m,
            }
        )
    return {"sessions": out}


@router.post("/sessions")
async def create_session(
    body: SessionCreateIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Demo mode: keep all producers on one long-lived open session unless caller provides an explicit user id.
    external_user_id = body.external_user_id or "desktop-cognisense"

    existing_res = await db.execute(
        select(SessionRow)
        .where(SessionRow.external_user_id == external_user_id)
        .where(SessionRow.ended_at.is_(None))
        .order_by(SessionRow.started_at.desc())
        .limit(1)
    )
    existing = existing_res.scalar_one_or_none()
    if existing is not None:
        return {
            "id": str(existing.id),
            "started_at": existing.started_at.isoformat() if existing.started_at else None,
        }

    row = SessionRow(
        external_user_id=external_user_id,
        client_meta=body.client_meta or {},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": str(row.id), "started_at": row.started_at.isoformat() if row.started_at else None}


@router.post("/sessions/{session_id}/snapshots")
async def ingest_snapshot(
    session_id: UUID,
    payload: SnapshotPayloadIn,
    db: AsyncSession = Depends(get_db),
    buffer: SnapshotBuffer = Depends(get_snapshot_buffer),
    settings: Settings = Depends(get_settings),
) -> dict:
    r = await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    session_row = r.scalar_one_or_none()

    # Always converge to the latest open session for this user
    user_id = session_row.external_user_id if session_row else "desktop-cognisense"
    if user_id:
        latest_res = await db.execute(
            select(SessionRow)
            .where(SessionRow.external_user_id == user_id)
            .where(SessionRow.ended_at.is_(None))
            .order_by(SessionRow.started_at.desc())
            .limit(1)
        )
        latest = latest_res.scalar_one_or_none()
        if latest is not None:
            session_row = latest
            session_id = latest.id

    if session_row is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    normalized = normalize_snapshot(payload, fallback_ts=datetime.now(timezone.utc))
    buffer.append(session_id, normalized)

    await process_minute_for_session(db, buffer, settings, session_id)
    await db.commit()
    return {
        "ok": True,
        "session_id": str(session_id),
        "buffer_len": len(buffer.window(session_id)),
        "current_minute_bucket": floor_to_minute_utc(datetime.now(timezone.utc)).isoformat(),
    }

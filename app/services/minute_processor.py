"""Once per minute: for each active session, compute dashboard row from buffer."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import DashboardMetricRow, SessionRow
from app.services.ai_pipeline import SessionSummary, build_ai_request, run_ai_or_rules
from app.services.buffer import SnapshotBuffer, floor_to_minute_utc


logger = logging.getLogger(__name__)


async def process_minute_for_session(
    db: AsyncSession,
    buffer: SnapshotBuffer,
    settings: Settings,
    session_id: UUID,
    now: datetime | None = None,
) -> None:
    """
    Idempotent per (session, bucket_start): upsert dashboard_metrics.
    Call from APScheduler / Celery beat aligned to wall clock.
    """
    now = now or datetime.now(timezone.utc)
    bucket = floor_to_minute_utc(now)

    last = buffer.get_last_bucket(session_id)
    if last == bucket:
        return

    window = buffer.window(session_id)
    if not window:
        buffer.set_last_bucket(session_id, bucket)
        return

    res = await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    row = res.scalar_one_or_none()
    if row is None:
        return

    summary = SessionSummary(
        session_started_at=row.started_at.isoformat() if row.started_at else None,
        extra={"client_meta_keys": list((row.client_meta or {}).keys())},
    )
    req = build_ai_request(window, summary)
    features, inference = await run_ai_or_rules(req, settings)

    values = {
        "session_id": session_id,
        "bucket_start": bucket,
        "avg_heart_rate": features.avg_heart_rate,
        "avg_hrv": features.avg_hrv,
        "avg_spo2": features.avg_spo2,
        "stress_index": features.stress_index,
        "fatigue_index": features.fatigue_index,
        "focus_percentage": features.focus_percentage,
        "fatigue_percentage": features.fatigue_percentage,
        "confusion_percentage": features.confusion_percentage,
        "productivity_score": features.productivity_score,
        "state_label": features.state_label,
        "model_version": settings.model_version,
        "feature_meta": {
            "window_len": len(window),
            "inference": inference,
            "openrouter_model": settings.openrouter_model if inference == "openrouter" else None,
        },

    }

    insert_stmt = pg_insert(DashboardMetricRow).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_dashboard_session_bucket",
        set_={
            "avg_heart_rate": insert_stmt.excluded.avg_heart_rate,
            "avg_hrv": insert_stmt.excluded.avg_hrv,
            "avg_spo2": insert_stmt.excluded.avg_spo2,
            "stress_index": insert_stmt.excluded.stress_index,
            "fatigue_index": insert_stmt.excluded.fatigue_index,
            "focus_percentage": insert_stmt.excluded.focus_percentage,
            "fatigue_percentage": insert_stmt.excluded.fatigue_percentage,
            "confusion_percentage": insert_stmt.excluded.confusion_percentage,
            "productivity_score": insert_stmt.excluded.productivity_score,
            "state_label": insert_stmt.excluded.state_label,
            "model_version": insert_stmt.excluded.model_version,
            "feature_meta": insert_stmt.excluded.feature_meta,
        },
    )
    await db.execute(upsert_stmt)
    buffer.set_last_bucket(session_id, bucket)


async def process_all_open_sessions(
    db: AsyncSession,
    buffer: SnapshotBuffer,
    settings: Settings,
) -> None:
    res = await db.execute(select(SessionRow.id).where(SessionRow.ended_at.is_(None)))
    for (sid,) in res.all():
        try:
            await process_minute_for_session(db, buffer, settings, sid)
        except Exception:
            logger.exception("Minute processing failed for session_id=%s", sid)

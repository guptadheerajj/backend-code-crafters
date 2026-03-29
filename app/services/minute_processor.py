"""Once per minute: for each active session, compute dashboard row from buffer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import DashboardMetricRow, FaceScanMetricRow, SessionRow
from app.services.ai_pipeline import AIDashboardFeatures, SessionSummary, build_ai_request, run_ai_or_rules
from app.services.buffer import SnapshotBuffer, floor_to_minute_utc


logger = logging.getLogger(__name__)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_if_none(value: float | None, default: float) -> float:
    return default if value is None else value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _percent_to_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    if value > 1.0:
        return _clamp(value / 100.0, 0.0, 1.0)
    return _clamp(value, 0.0, 1.0)


def _fill_dashboard_defaults(features: AIDashboardFeatures) -> AIDashboardFeatures:
    avg_hr = _clamp(_default_if_none(_safe_float(features.avg_heart_rate), 75.0), 60.0, 100.0)
    avg_hrv = _clamp(_default_if_none(_safe_float(features.avg_hrv), 42.0), 20.0, 90.0)
    avg_spo2 = _clamp(_default_if_none(_safe_float(features.avg_spo2), 98.0), 95.0, 100.0)

    stress = _clamp(_default_if_none(_safe_float(features.stress_index), 0.35), 0.0, 1.0)
    fatigue = _clamp(_default_if_none(_safe_float(features.fatigue_index), 0.30), 0.0, 1.0)

    focus_pct = _clamp(_default_if_none(_safe_float(features.focus_percentage), 55.0), 0.0, 100.0)
    fatigue_pct = _clamp(_default_if_none(_safe_float(features.fatigue_percentage), 28.0), 0.0, 100.0)
    confusion_pct = _clamp(_default_if_none(_safe_float(features.confusion_percentage), 18.0), 0.0, 100.0)
    productivity = _clamp(_default_if_none(_safe_float(features.productivity_score), 62.0), 0.0, 100.0)

    label = features.state_label
    if label not in {"idle", "focus", "fatigue", "confused"}:
        label = "idle"
    if label == "idle":
        winner = max(
            (("focus", focus_pct), ("fatigue", fatigue_pct), ("confused", confusion_pct)),
            key=lambda item: item[1],
        )
        if winner[1] >= 15.0:
            label = winner[0]

    return AIDashboardFeatures(
        avg_heart_rate=avg_hr,
        avg_hrv=avg_hrv,
        avg_spo2=avg_spo2,
        stress_index=stress,
        fatigue_index=fatigue,
        focus_percentage=focus_pct,
        fatigue_percentage=fatigue_pct,
        confusion_percentage=confusion_pct,
        productivity_score=productivity,
        state_label=label,
    )


async def _merge_latest_face_scan(
    db: AsyncSession,
    session_id: UUID,
    bucket: datetime,
    features: AIDashboardFeatures,
) -> tuple[AIDashboardFeatures, dict]:
    lookback = bucket - timedelta(minutes=15)
    lookahead = bucket + timedelta(minutes=1)

    res = await db.execute(
        select(FaceScanMetricRow)
        .where(FaceScanMetricRow.session_id == session_id)
        .where(FaceScanMetricRow.scanned_at >= lookback)
        .where(FaceScanMetricRow.scanned_at <= lookahead)
        .where(FaceScanMetricRow.status == "ok")
        .order_by(FaceScanMetricRow.scanned_at.desc(), FaceScanMetricRow.id.desc())
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        return features, {"source": "none"}

    mm = ((row.metrics_meta or {}).get("missing_metrics") or {})

    def pick(current, *candidates):
        if current is not None:
            return current
        for c in candidates:
            if c is not None:
                return c
        return None

    stress_from_mm = _safe_float(mm.get("stress_index"))
    fatigue_from_mm = _safe_float(mm.get("fatigue_index"))

    if stress_from_mm is not None and stress_from_mm > 1.0:
        stress_from_mm = _clamp(stress_from_mm / 100.0, 0.0, 1.0)
    if fatigue_from_mm is not None and fatigue_from_mm > 1.0:
        fatigue_from_mm = _clamp(fatigue_from_mm / 100.0, 0.0, 1.0)

    merged = features.model_copy(
        update={
            "avg_heart_rate": pick(features.avg_heart_rate, _safe_float(mm.get("avg_heart_rate"))),
            "avg_hrv": pick(features.avg_hrv, _safe_float(mm.get("avg_hrv"))),
            "avg_spo2": pick(features.avg_spo2, _safe_float(mm.get("avg_spo2"))),
            "stress_index": pick(features.stress_index, stress_from_mm),
            "fatigue_index": pick(features.fatigue_index, fatigue_from_mm),
            "focus_percentage": pick(
                features.focus_percentage,
                _safe_float(mm.get("focus_percentage")),
                (_percent_to_ratio(row.attention_score) or 0.0) * 100.0,
            ),
            "fatigue_percentage": pick(
                features.fatigue_percentage,
                _safe_float(mm.get("fatigue_percentage")),
                (_percent_to_ratio(row.fatigue_signal) or 0.0) * 100.0,
            ),
            "confusion_percentage": pick(features.confusion_percentage, _safe_float(mm.get("confusion_percentage"))),
            "productivity_score": pick(features.productivity_score, _safe_float(mm.get("productivity_score"))),
        }
    )

    return merged, {
        "source": "face_scan_metrics",
        "face_scan_id": row.id,
        "scanned_at": row.scanned_at.isoformat(),
    }


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
    features, face_scan_meta = await _merge_latest_face_scan(db, session_id, bucket, features)
    features = _fill_dashboard_defaults(features)

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
            "face_scan": face_scan_meta,
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

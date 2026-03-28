from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    CognitiveStatePrediction,
    SessionRecord,
    SessionSummary,
    Snapshot,
)
from app.schemas.snapshot import PredictionSummaryOut, SnapshotIngestResponse
from app.services import buffer
from app.services.inference import build_model_input, canonical_digest, run_stub_rules
from app.services.summary_merge import merge_payload_into_summary
from app.services.temporal_features import compute_window_features


def _snapshot_record_for_buffer(row: Snapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "captured_at": row.captured_at.isoformat(),
        "client_seq": row.client_seq,
        "payload": row.payload,
    }


async def _get_or_create_summary(db: AsyncSession, session_id: uuid.UUID) -> SessionSummary:
    r = await db.execute(select(SessionSummary).where(SessionSummary.session_id == session_id))
    s = r.scalar_one_or_none()
    if s:
        return s
    s = SessionSummary(session_id=session_id, snapshot_count=0, summary={})
    db.add(s)
    await db.flush()
    return s


async def _load_existing_snapshot(
    db: AsyncSession,
    session_id: uuid.UUID,
    body,
) -> Snapshot | None:
    if body.idempotency_key:
        r = await db.execute(
            select(Snapshot).where(
                Snapshot.session_id == session_id,
                Snapshot.idempotency_key == body.idempotency_key,
            )
        )
        hit = r.scalar_one_or_none()
        if hit:
            return hit
    if body.client_seq is not None:
        r = await db.execute(
            select(Snapshot).where(
                Snapshot.session_id == session_id,
                Snapshot.client_seq == body.client_seq,
            )
        )
        hit = r.scalar_one_or_none()
        if hit:
            return hit
    return None


async def ingest_snapshot(
    db: AsyncSession,
    redis_client: redis.Redis,
    session_id: uuid.UUID,
    body,
) -> SnapshotIngestResponse:
    sess = await db.get(SessionRecord, session_id)
    if not sess or sess.status != "active":
        raise HTTPException(status_code=404, detail="Session not found or not active")

    existing = await _load_existing_snapshot(db, session_id, body)
    created = False
    if existing:
        snap = existing
    else:
        snap = Snapshot(
            session_id=session_id,
            captured_at=body.captured_at,
            client_seq=body.client_seq,
            idempotency_key=body.idempotency_key,
            payload=body.payload,
        )
        db.add(snap)
        await db.flush()
        created = True

    summary_row = await _get_or_create_summary(db, session_id)
    if created:
        summary_row.snapshot_count = int(summary_row.snapshot_count or 0) + 1
        if summary_row.first_snapshot_at is None:
            summary_row.first_snapshot_at = snap.captured_at
        summary_row.last_snapshot_at = snap.captured_at
        merge_payload_into_summary(
            summary_row.summary or {}, snap.payload, captured_at=snap.captured_at
        )
        await buffer.push_snapshot(
            redis_client,
            session_id,
            _snapshot_record_for_buffer(snap),
        )

    pred_summary = PredictionSummaryOut(triggered=False)

    buf = await buffer.list_snapshots(redis_client, session_id)
    if len(buf) >= settings.snapshot_buffer_size:
        got_lock = await buffer.acquire_prediction_lock(redis_client, session_id)
        if got_lock:
            ordered = sorted(
                buf,
                key=lambda x: datetime.fromisoformat(str(x["captured_at"]).replace("Z", "+00:00")),
            )
            window_features = compute_window_features(ordered)
            inference_id = uuid.uuid4()
            model_input = build_model_input(
                session_id=session_id,
                inference_id=inference_id,
                recent_window=ordered,
                window_features=window_features,
                session_summary=dict(summary_row.summary or {}),
            )
            digest = canonical_digest(model_input)
            out = run_stub_rules(model_input)

            ids = [int(x["id"]) for x in ordered if x.get("id") is not None]
            ws = datetime.fromisoformat(str(ordered[0]["captured_at"]).replace("Z", "+00:00"))
            we = datetime.fromisoformat(str(ordered[-1]["captured_at"]).replace("Z", "+00:00"))

            pred = CognitiveStatePrediction(
                session_id=session_id,
                snapshot_ids=ids,
                window_start=ws,
                window_end=we,
                model_name=settings.model_name,
                model_version=settings.model_version,
                state_label=out["state_label"],
                state_scores=out["state_scores"],
                temporal_features=window_features,
                explanation=out.get("explanation"),
                input_digest=digest,
            )
            db.add(pred)
            await db.flush()

            sess.latest_state_label = out["state_label"]
            sess.latest_state_at = datetime.now(timezone.utc)

            pred_summary = PredictionSummaryOut(
                triggered=True,
                prediction_id=pred.id,
                state_label=out["state_label"],
                state_scores=out["state_scores"],
                window={"start": ws.isoformat(), "end": we.isoformat()},
            )

    return SnapshotIngestResponse(
        snapshot_id=snap.id,
        session_id=session_id,
        prediction=pred_summary,
    )

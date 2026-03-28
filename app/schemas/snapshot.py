import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SnapshotIngest(BaseModel):
    captured_at: datetime
    client_seq: int | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class PredictionSummaryOut(BaseModel):
    triggered: bool
    prediction_id: int | None = None
    state_label: str | None = None
    state_scores: dict[str, Any] | None = None
    window: dict[str, str] | None = None


class SnapshotIngestResponse(BaseModel):
    snapshot_id: int
    session_id: uuid.UUID
    prediction: PredictionSummaryOut

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class TimelineItemType(str, Enum):
    snapshot = "snapshot"
    prediction = "prediction"


class TimelineSnapshotData(BaseModel):
    snapshot_id: int
    client_seq: int | None
    captured_at: datetime
    payload_summary: dict[str, Any]


class TimelinePredictionData(BaseModel):
    prediction_id: int
    state_label: str
    state_scores: dict[str, Any]
    computed_at: datetime


class TimelineItem(BaseModel):
    type: TimelineItemType
    at: datetime
    data: TimelineSnapshotData | TimelinePredictionData


class TimelinePage(BaseModel):
    session_id: uuid.UUID
    items: list[TimelineItem]
    next_cursor: str | None

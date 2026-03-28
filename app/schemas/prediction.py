import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class PredictionOut(BaseModel):
    id: int
    session_id: uuid.UUID
    computed_at: datetime
    window_start: datetime
    window_end: datetime
    model_name: str
    model_version: str
    state_label: str
    state_scores: dict[str, Any]
    temporal_features: dict[str, Any]
    explanation: dict[str, Any] | None

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

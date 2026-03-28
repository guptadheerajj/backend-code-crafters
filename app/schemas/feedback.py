import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    feedback_type: str
    prediction_id: int | None = None
    label: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class FeedbackOut(BaseModel):
    id: int
    session_id: uuid.UUID
    prediction_id: int | None
    created_at: datetime
    feedback_type: str
    label: str | None
    rating: int | None
    comment: str | None

    model_config = {"from_attributes": True}

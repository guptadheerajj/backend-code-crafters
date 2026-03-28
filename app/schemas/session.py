import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    user_id: uuid.UUID
    device_id: str | None = None
    external_ref: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SessionEnd(BaseModel):
    status: str = "ended"  # ended | abandoned


class SessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    device_id: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    meta: dict[str, Any]
    latest_state_label: str | None = None
    latest_state_at: datetime | None = None

    model_config = {"from_attributes": True}

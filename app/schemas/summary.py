import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SessionSummaryOut(BaseModel):
    session_id: uuid.UUID
    snapshot_count: int
    first_snapshot_at: datetime | None
    last_snapshot_at: datetime | None
    summary: dict[str, Any]
    summary_version: int
    updated_at: datetime

    model_config = {"from_attributes": True}

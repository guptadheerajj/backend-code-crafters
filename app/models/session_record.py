import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.feedback import FeedbackLog
    from app.models.prediction import CognitiveStatePrediction
    from app.models.snapshot import Snapshot
    from app.models.summary import SessionSummary


class SessionRecord(Base):
    """User / device session collecting snapshots."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active", index=True
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    latest_state_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_state_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshots: Mapped[list["Snapshot"]] = relationship(
        "Snapshot", back_populates="session", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["CognitiveStatePrediction"]] = relationship(
        "CognitiveStatePrediction", back_populates="session", cascade="all, delete-orphan"
    )
    summary: Mapped["SessionSummary | None"] = relationship(
        "SessionSummary", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    feedback: Mapped[list["FeedbackLog"]] = relationship(
        "FeedbackLog", back_populates="session", cascade="all, delete-orphan"
    )

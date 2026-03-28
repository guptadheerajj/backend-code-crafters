import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ARRAY, BigInteger, DateTime, ForeignKey, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.feedback import FeedbackLog
    from app.models.session_record import SessionRecord


class CognitiveStatePrediction(Base):
    __tablename__ = "cognitive_state_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    snapshot_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)

    state_label: Mapped[str] = mapped_column(Text, nullable=False)
    state_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    temporal_features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    input_digest: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["SessionRecord"] = relationship("SessionRecord", back_populates="predictions")
    feedback: Mapped[list["FeedbackLog"]] = relationship(
        "FeedbackLog", back_populates="prediction"
    )

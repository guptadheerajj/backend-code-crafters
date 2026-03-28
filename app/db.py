from collections.abc import AsyncGenerator
from datetime import datetime
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    external_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class DashboardMetricRow(Base):
    __tablename__ = "dashboard_metrics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    avg_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hrv: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_spo2: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    fatigue_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    focus_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    fatigue_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    confusion_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    productivity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    state_label: Mapped[str] = mapped_column(String(32), nullable=False, server_default="idle")
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (UniqueConstraint("session_id", "bucket_start", name="uq_dashboard_session_bucket"),)


engine = create_async_engine(get_settings().database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Ensure DB is reachable and required tables exist before serving traffic.
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        logger.exception("Database connectivity/init failed")
        raise

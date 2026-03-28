"""initial schema

Revision ID: 20260328_01
Revises:
Create Date: 2026-03-28

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260328_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("latest_state_label", sa.Text(), nullable=True),
        sa.Column("latest_state_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="sessions_end_after_start"),
        sa.CheckConstraint("status IN ('active', 'ended', 'abandoned')", name="sessions_status_check"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_external_ref", "sessions", ["external_ref"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index("ix_sessions_status", "sessions", ["status"], unique=False)
    op.create_index("idx_sessions_user_started", "sessions", ["user_id", "started_at"], unique=False)

    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_seq", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingest_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_snapshots_session_seq",
        "snapshots",
        ["session_id", "client_seq"],
        unique=True,
        postgresql_where=sa.text("client_seq IS NOT NULL"),
    )
    op.create_index(
        "uq_snapshots_idempotency",
        "snapshots",
        ["session_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("idx_snapshots_session_time", "snapshots", ["session_id", "captured_at"], unique=False)

    op.create_table(
        "cognitive_state_predictions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("snapshot_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("state_label", sa.Text(), nullable=False),
        sa.Column(
            "state_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("temporal_features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_digest", sa.Text(), nullable=True),
        sa.CheckConstraint("window_end >= window_start", name="prediction_window"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_pred_session_time",
        "cognitive_state_predictions",
        ["session_id", "computed_at"],
        unique=False,
    )

    op.create_table(
        "session_summaries",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "feedback_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("feedback_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["cognitive_state_predictions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_feedback_session", "feedback_logs", ["session_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_feedback_session", table_name="feedback_logs")
    op.drop_table("feedback_logs")
    op.drop_table("session_summaries")

    op.drop_index("idx_pred_session_time", table_name="cognitive_state_predictions")
    op.drop_table("cognitive_state_predictions")

    op.drop_index("idx_snapshots_session_time", table_name="snapshots")
    op.drop_index("uq_snapshots_idempotency", table_name="snapshots", postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    op.drop_index("uq_snapshots_session_seq", table_name="snapshots", postgresql_where=sa.text("client_seq IS NOT NULL"))
    op.drop_table("snapshots")

    op.drop_index("idx_sessions_user_started", table_name="sessions")
    op.drop_index("ix_sessions_status", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_index("ix_sessions_external_ref", table_name="sessions")
    op.drop_table("sessions")

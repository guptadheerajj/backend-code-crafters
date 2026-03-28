-- Cognitive state monitoring — dashboard-only persistence (PostgreSQL 14+)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- sessions: one row per monitoring session (extension connect → disconnect)
-- ---------------------------------------------------------------------------
CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_user_id TEXT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at         TIMESTAMPTZ NULL,
    client_meta      JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_started_at ON sessions (started_at DESC);
CREATE INDEX idx_sessions_external_user ON sessions (external_user_id) WHERE external_user_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- dashboard_metrics: one row per session per 1-minute wall-clock bucket
-- Only dashboard-ready features; no raw snapshots.
-- ---------------------------------------------------------------------------
CREATE TABLE dashboard_metrics (
    id                     BIGSERIAL PRIMARY KEY,
    session_id             UUID NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    bucket_start           TIMESTAMPTZ NOT NULL,
    -- Physio (nullable until wearables integrated)
    avg_heart_rate         DOUBLE PRECISION NULL,
    avg_hrv                DOUBLE PRECISION NULL,
    avg_spo2               DOUBLE PRECISION NULL,
    -- Behavioral / derived (from window + rules/AI)
    stress_index           DOUBLE PRECISION NULL,
    fatigue_index          DOUBLE PRECISION NULL,
    focus_percentage       DOUBLE PRECISION NULL CHECK (focus_percentage IS NULL OR (focus_percentage >= 0 AND focus_percentage <= 100)),
    fatigue_percentage     DOUBLE PRECISION NULL CHECK (fatigue_percentage IS NULL OR (fatigue_percentage >= 0 AND fatigue_percentage <= 100)),
    confusion_percentage   DOUBLE PRECISION NULL CHECK (confusion_percentage IS NULL OR (confusion_percentage >= 0 AND confusion_percentage <= 100)),
    productivity_score     DOUBLE PRECISION NULL,
    state_label            TEXT NOT NULL DEFAULT 'idle',
    -- Provenance / debugging without storing raw payloads
    model_version          TEXT NULL,
    feature_meta           JSONB NOT NULL DEFAULT '{}',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_dashboard_session_bucket UNIQUE (session_id, bucket_start),
    CONSTRAINT chk_state_label CHECK (
        state_label IN ('focus', 'fatigue', 'confused', 'idle')
    )
);

CREATE INDEX idx_dashboard_session_bucket ON dashboard_metrics (session_id, bucket_start DESC);

-- ---------------------------------------------------------------------------
-- feedback_logs (optional): user corrections for calibration / training
-- ---------------------------------------------------------------------------
CREATE TABLE feedback_logs (
    id           BIGSERIAL PRIMARY KEY,
    session_id   UUID NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    feedback_type TEXT NOT NULL,
    payload      JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_feedback_session ON feedback_logs (session_id, recorded_at DESC);

-- Touch updated_at on sessions (optional; call from app on snapshot activity)
CREATE OR REPLACE FUNCTION touch_session_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sessions_updated
    BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE PROCEDURE touch_session_updated_at();

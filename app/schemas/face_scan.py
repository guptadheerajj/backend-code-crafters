from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FaceStatsIn(BaseModel):
    sampled_frames: int | None = None
    face_frames: int | None = None
    face_presence_ratio: float | None = None
    transitions: int | None = None
    signal_quality: float | None = None


class MissingMetricsIn(BaseModel):
    avg_heart_rate: float | None = None
    avg_hrv: float | None = None
    avg_spo2: float | None = None
    stress_index: float | None = None
    fatigue_index: float | None = None
    focus_percentage: float | None = None
    fatigue_percentage: float | None = None
    confusion_percentage: float | None = None
    productivity_score: float | None = None


class FaceScanSubmitIn(BaseModel):
    scanned_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    status: str = "ok"
    error_message: str | None = None

    frames_total: int | None = Field(default=None, ge=0)
    frames_recognized: int | None = Field(default=None, ge=0)
    recognition_ratio: float | None = None
    avg_face_confidence: float | None = None
    attention_score: float | None = None
    fatigue_signal: float | None = None

    missing_metrics: MissingMetricsIn | None = None
    face_stats: FaceStatsIn | None = None
    rppg: dict[str, Any] | None = None
    metrics_meta: dict[str, Any] = Field(default_factory=dict)


class FaceScanOut(BaseModel):
    id: int
    session_id: str
    scanned_at: str
    status: str
    recognition_ratio: float | None = None
    attention_score: float | None = None
    fatigue_signal: float | None = None
    missing_metrics: dict[str, Any] = Field(default_factory=dict)

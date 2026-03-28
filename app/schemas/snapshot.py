"""Normalized snapshot shape after cleaning (in-memory / AI input)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KeyboardIn(BaseModel):
    wpm_estimate: float | None = None


class MouseIn(BaseModel):
    clicks_per_min: float | None = None
    distance_px_estimate: float | None = None


class TabIn(BaseModel):
    switch_freq_per_min: float | None = None
    active_domain_hash: str | None = None


class SnapshotPayloadIn(BaseModel):
    """Raw-ish payload from extension; field names match frontend contract."""
    ts: datetime | None = None
    keyboard: KeyboardIn | dict[str, Any] | None = None
    mouse: MouseIn | dict[str, Any] | None = None
    tab: TabIn | dict[str, Any] | None = None
    stress_index: float | None = None
    fatigue_index: float | None = None
    # Optional physio if ever sent from a bridge
    heart_rate: float | None = None
    hrv: float | None = None
    spo2: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedSnapshot(BaseModel):
    """Canonical snapshot for buffer + AI window."""
    ts: datetime
    wpm: float | None = None
    tab_switch_rate: float | None = None
    stress_index: float | None = None
    fatigue_index: float | None = None
    heart_rate: float | None = None
    hrv: float | None = None
    spo2: float | None = None


class SessionCreateIn(BaseModel):
    external_user_id: str | None = None
    client_meta: dict[str, Any] = Field(default_factory=dict)

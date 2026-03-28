"""Map frontend payload → backend fields; safe defaults for missing data."""

from datetime import datetime, timezone

from app.schemas.snapshot import NormalizedSnapshot, SnapshotPayloadIn


def _as_dict(obj: object) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    return {}


def normalize_snapshot(payload: SnapshotPayloadIn, fallback_ts: datetime | None = None) -> NormalizedSnapshot:
    kb = _as_dict(payload.keyboard)
    tab = _as_dict(payload.tab)

    wpm = kb.get("wpm_estimate")
    if wpm is not None:
        try:
            wpm = float(wpm)
        except (TypeError, ValueError):
            wpm = None

    tab_switch = tab.get("switch_freq_per_min")
    if tab_switch is not None:
        try:
            tab_switch = float(tab_switch)
        except (TypeError, ValueError):
            tab_switch = None

    def f_or_none(name: str) -> float | None:
        v = getattr(payload, name, None)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ts = payload.ts or fallback_ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return NormalizedSnapshot(
        ts=ts,
        wpm=wpm,
        tab_switch_rate=tab_switch,
        stress_index=f_or_none("stress_index"),
        fatigue_index=f_or_none("fatigue_index"),
        heart_rate=f_or_none("heart_rate"),
        hrv=f_or_none("hrv"),
        spo2=f_or_none("spo2"),
    )

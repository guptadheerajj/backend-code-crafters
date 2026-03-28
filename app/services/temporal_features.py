from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Literal


def _nums(items: list[dict[str, Any]], path: tuple[str, ...]) -> list[float]:
    out: list[float] = []
    for it in items:
        payload = it.get("payload") or {}
        cur: Any = payload
        for p in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(p)
        if isinstance(cur, int | float) and not isinstance(cur, bool):
            out.append(float(cur))
    return out


def _trend_label(values: list[float]) -> Literal["up", "down", "stable"] | None:
    if len(values) < 2:
        return None
    slope = (values[-1] - values[0]) / max(len(values) - 1, 1)
    if slope > 0.05:
        return "up"
    if slope < -0.05:
        return "down"
    return "stable"


def compute_window_features(buffer_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Trends over the rolling buffer (already time-ordered ascending)."""
    items = sorted(
        buffer_items,
        key=lambda x: datetime.fromisoformat(str(x["captured_at"]).replace("Z", "+00:00")),
    )
    hr = _nums(items, ("camera", "hr_bpm"))
    wpm = _nums(items, ("keyboard", "wpm"))
    switches = _nums(items, ("tab", "switches"))
    eye = _nums(items, ("camera", "eye_openness"))

    features: dict[str, Any] = {
        "hr_trend": _trend_label(hr),
        "wpm_trend": _trend_label(wpm),
        "tab_switch_trend": _trend_label(switches),
        "eye_openness_trend": _trend_label(eye),
        "buffer_len": len(items),
    }

    if hr:
        features["hr_bpm_mean"] = round(statistics.mean(hr), 3)
    if wpm:
        features["wpm_mean"] = round(statistics.mean(wpm), 3)
    return features

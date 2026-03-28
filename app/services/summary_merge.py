"""Incremental session summary updates (EMA-style, extensible)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def _get_path(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _ema(prev: float | None, value: float, alpha: float = 0.2) -> float:
    if prev is None or math.isnan(prev):
        return value
    return alpha * value + (1 - alpha) * prev


def merge_payload_into_summary(
    summary: dict[str, Any],
    payload: dict[str, Any],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    """Mutates and returns `summary` with online aggregates."""
    ag = summary.setdefault("aggregates", {})

    wpm = _get_path(payload, ("keyboard", "wpm"))
    if isinstance(wpm, int | float) and not isinstance(wpm, bool):
        block = ag.setdefault("wpm", {})
        block["ema"] = _ema(block.get("ema"), float(wpm))
        n = int(block.get("n", 0)) + 1
        prev_mean = float(block.get("mean", 0.0))
        block["mean"] = prev_mean + (float(wpm) - prev_mean) / n
        block["n"] = n

    hr = _get_path(payload, ("camera", "hr_bpm"))
    if isinstance(hr, int | float) and not isinstance(hr, bool):
        block = ag.setdefault("hr_bpm", {})
        block["ema"] = _ema(block.get("ema"), float(hr))

    stress = _get_path(payload, ("derived", "stress_index"))
    if isinstance(stress, int | float) and not isinstance(stress, bool):
        block = ag.setdefault("stress_index", {})
        block["ema"] = _ema(block.get("ema"), float(stress))

    fatigue = _get_path(payload, ("derived", "fatigue_index"))
    if isinstance(fatigue, int | float) and not isinstance(fatigue, bool):
        block = ag.setdefault("fatigue_index", {})
        block["ema"] = _ema(block.get("ema"), float(fatigue))

    switches = _get_path(payload, ("tab", "switches"))
    if isinstance(switches, int | float) and not isinstance(switches, bool):
        block = ag.setdefault("tab_switches_per_snapshot", {})
        block["ema"] = _ema(block.get("ema"), float(switches))

    dq = summary.setdefault("data_quality", {})
    last_at_str = dq.get("last_captured_at")
    if isinstance(last_at_str, str):
        try:
            last_dt = datetime.fromisoformat(last_at_str.replace("Z", "+00:00"))
            gap_sec = (captured_at - last_dt).total_seconds()
            if gap_sec > 45:
                dq["delayed_snapshots"] = int(dq.get("delayed_snapshots", 0)) + 1
        except ValueError:
            pass
    dq["last_captured_at"] = captured_at.isoformat()

    return summary

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from app.config import settings


def build_model_input(
    *,
    session_id: uuid.UUID,
    inference_id: uuid.UUID,
    recent_window: list[dict[str, Any]],
    window_features: dict[str, Any],
    session_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "model_input_v1",
        "session_id": str(session_id),
        "inference_id": str(inference_id),
        "model": {"name": settings.model_name, "version": settings.model_version},
        "window": {
            "start": recent_window[0]["captured_at"],
            "end": recent_window[-1]["captured_at"],
            "snapshots": [
                {
                    "id": it.get("id"),
                    "captured_at": it["captured_at"],
                    "seq": it.get("client_seq"),
                    "payload": it.get("payload", {}),
                }
                for it in recent_window
            ],
            "window_features": window_features,
        },
        "session_summary": session_summary,
    }


def canonical_digest(model_input: dict[str, Any]) -> str:
    blob = json.dumps(model_input, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run_stub_rules(model_input: dict[str, Any]) -> dict[str, Any]:
    """
    Replace with ONNX / HTTP model service. Computes a deterministic placeholder.
    """
    window = model_input.get("window") or {}
    snaps = window.get("snapshots") or []
    wf = window.get("window_features") or {}
    summary = model_input.get("session_summary") or {}
    aggregates = summary.get("aggregates") or {}

    stress_ema = (aggregates.get("stress_index") or {}).get("ema")
    fatigue_ema = (aggregates.get("fatigue_index") or {}).get("ema")
    hr_tr = wf.get("hr_trend")
    wpm_tr = wf.get("wpm_trend")

    stress = float(stress_ema or 0.0)
    fatigue = float(fatigue_ema or 0.0)

    if fatigue > 0.55 or hr_tr == "down" and wpm_tr == "down":
        label = "mild_fatigue"
        scores = {"focused": 0.25, "mild_fatigue": 0.55, "stressed": 0.2}
    elif stress > 0.55 or wpm_tr == "up":
        label = "stressed"
        scores = {"focused": 0.25, "mild_fatigue": 0.2, "stressed": 0.55}
    else:
        label = "focused"
        scores = {"focused": 0.65, "mild_fatigue": 0.2, "stressed": 0.15}

    explanation = {
        "signals": [
            {"key": "stress_ema", "value": stress},
            {"key": "fatigue_ema", "value": fatigue},
            {"key": "hr_trend", "value": hr_tr},
            {"key": "wpm_trend", "value": wpm_tr},
            {"key": "snapshots_in_window", "value": len(snaps)},
        ]
    }

    return {
        "state_label": label,
        "state_scores": scores,
        "explanation": explanation,
    }

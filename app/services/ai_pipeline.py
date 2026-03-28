"""AI / rules engine: window + optional session summary → dashboard features."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.schemas.snapshot import NormalizedSnapshot

logger = logging.getLogger(__name__)

_STATE_LABELS = frozenset({"idle", "focus", "fatigue", "confused"})


class RecentWindowItem(BaseModel):
    ts: str
    wpm: float | None = None
    tab_switch_rate: float | None = None
    stress_index: float | None = None
    fatigue_index: float | None = None
    heart_rate: float | None = None
    hrv: float | None = None
    spo2: float | None = None


class SessionSummary(BaseModel):
    session_started_at: str | None = None
    snapshot_count_session: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class AIRequestBody(BaseModel):
    """Contract sent to external model or internal rules."""
    recent_window: list[RecentWindowItem]
    summary: SessionSummary | None = None


class AIDashboardFeatures(BaseModel):
    avg_heart_rate: float | None = None
    avg_hrv: float | None = None
    avg_spo2: float | None = None
    stress_index: float | None = None
    fatigue_index: float | None = None
    focus_percentage: float | None = None
    fatigue_percentage: float | None = None
    confusion_percentage: float | None = None
    productivity_score: float | None = None
    state_label: str = "idle"


def build_ai_request(
    window: list[NormalizedSnapshot],
    summary: SessionSummary | None = None,
) -> AIRequestBody:
    items = [
        RecentWindowItem(
            ts=s.ts.isoformat(),
            wpm=s.wpm,
            tab_switch_rate=s.tab_switch_rate,
            stress_index=s.stress_index,
            fatigue_index=s.fatigue_index,
            heart_rate=s.heart_rate,
            hrv=s.hrv,
            spo2=s.spo2,
        )
        for s in window
    ]
    return AIRequestBody(recent_window=items, summary=summary)


def _avg(values: list[float | None]) -> float | None:
    xs = [v for v in values if v is not None]
    if not xs:
        return None
    return sum(xs) / len(xs)


def rules_engine_fallback(body: AIRequestBody) -> AIDashboardFeatures:
    """
    Placeholder when no LLM is configured: deterministic aggregates + simple state.
    Replace with HTTP call to your model returning the same shape.
    """
    w = body.recent_window
    if not w:
        return AIDashboardFeatures()

    stress = _avg([x.stress_index for x in w])
    fatigue = _avg([x.fatigue_index for x in w])
    hr = _avg([x.heart_rate for x in w])
    hrv = _avg([x.hrv for x in w])
    spo2 = _avg([x.spo2 for x in w])

    tab_sw = [x.tab_switch_rate for x in w if x.tab_switch_rate is not None]
    avg_tab = sum(tab_sw) / len(tab_sw) if tab_sw else None

    # Heuristic scores (0–100) — swap for model output
    focus_pct = None
    fatigue_pct = None
    confusion_pct = None
    if stress is not None and fatigue is not None:
        focus_pct = max(0.0, min(100.0,  100.0 - stress * 50.0 - fatigue * 30.0))
        fatigue_pct = max(0.0, min(100.0, fatigue * 100.0))
        confusion_pct = max(0.0, min(100.0, (avg_tab or 0) * 15.0)) if avg_tab else None

    prod = None
    if focus_pct is not None:
        prod = max(0.0, min(100.0, focus_pct * 0.7 + (100.0 - (fatigue_pct or 0)) * 0.3))

    label = "idle"
    if focus_pct is not None and fatigue_pct is not None and confusion_pct is not None:
        candidates = (
            ("focus", focus_pct),
            ("fatigue", fatigue_pct),
            ("confused", confusion_pct),
        )
        label, top = max(candidates, key=lambda p: p[1])
        if top < 20.0:
            label = "idle"

    return AIDashboardFeatures(
        avg_heart_rate=hr,
        avg_hrv=hrv,
        avg_spo2=spo2,
        stress_index=stress,
        fatigue_index=fatigue,
        focus_percentage=focus_pct,
        fatigue_percentage=fatigue_pct,
        confusion_percentage=confusion_pct,
        productivity_score=prod,
        state_label=label if label in ("focus", "fatigue", "confused", "idle") else "idle",
    )


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*)\n?```\s*$", t, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return t


def _parse_llm_features_json(raw: str) -> AIDashboardFeatures | None:
    try:
        data = json.loads(_strip_json_fence(raw))
        if not isinstance(data, dict):
            return None
        feat = AIDashboardFeatures.model_validate(data)
        if feat.state_label not in _STATE_LABELS:
            feat = feat.model_copy(update={"state_label": "idle"})
        return feat
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("LLM features JSON invalid: %s", e)
        return None


async def call_openrouter(body: AIRequestBody, settings: Settings) -> AIDashboardFeatures | None:
    key = (settings.openrouter_api_key or "").strip()
    if not key or not body.recent_window:
        return None

    base = settings.openrouter_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    window_json = json.dumps(body.model_dump(mode="json"), indent=2)

    system = (
        "You are a cognitive ergonomics analyst. Reply with one JSON object only, no markdown. "
        "Keys (use null where unknown): avg_heart_rate, avg_hrv, avg_spo2 (numbers), "
        "stress_index, fatigue_index (0–1 floats, consistent with the window), "
        "focus_percentage, fatigue_percentage, confusion_percentage, productivity_score (0–100), "
        "state_label (exactly one of: idle, focus, fatigue, confused). "
        "Ground estimates in the provided recent_window and summary; do not invent vital signs."
    )
    user = (
        "Produce the dashboard JSON for this window:\n\n"
        f"{window_json}\n\n"
        "Respond with the JSON object only."
    )

    req_body: dict[str, Any] = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    req_body["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_http_referer:
        headers["HTTP-Referer"] = settings.openrouter_http_referer
    if settings.openrouter_app_title:
        headers["X-Title"] = settings.openrouter_app_title

    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=req_body, headers=headers)
        if r.status_code == 400 and "response_format" in req_body:
            del req_body["response_format"]
            r = await client.post(url, json=req_body, headers=headers)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("OpenRouter HTTP %s: %s", r.status_code, e)
            return None
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            logger.warning("OpenRouter response shape unexpected: %s", e)
            return None

    if not isinstance(content, str):
        return None
    return _parse_llm_features_json(content)


async def run_ai_or_rules(body: AIRequestBody, settings: Settings) -> tuple[AIDashboardFeatures, str]:
    if settings.openrouter_api_key and body.recent_window:
        try:
            routed = await call_openrouter(body, settings)
            if routed is not None:
                return routed, "openrouter"
            logger.info("OpenRouter unavailable or parse failed; using rules fallback")
        except httpx.HTTPError as e:
            logger.warning("OpenRouter request error: %s", e)
        except Exception:
            logger.exception("OpenRouter failed; using rules fallback")

    return rules_engine_fallback(body), "rules"

from datetime import datetime, timezone
import os
import tempfile
from uuid import UUID
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import FaceScanMetricRow, SessionRow, get_db
from app.schemas.face_scan import FaceScanOut, FaceScanSubmitIn

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

router = APIRouter()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_optional_ratio(value):
    val = _to_float(value)
    if val is None:
        return None
    # Accept either 0-1 or 0-100 inputs.
    if val > 1.0:
        val = val / 100.0
    return _clamp(val, 0.0, 1.0)


def _analyze_video_sample(video_path: str) -> dict:
    result = {
        "frames_total": None,
        "frames_recognized": None,
        "recognition_ratio": None,
        "avg_face_confidence": None,
        "attention_score": None,
        "fatigue_signal": None,
        "metrics_meta": {
            "analyzer": "opencv-haar-sampled",
            "sample_rate_fps": "all",
            "error": None,
        },
    }
    if not CV2_AVAILABLE:
        result["metrics_meta"]["error"] = "opencv_not_installed"
        return result

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        result["metrics_meta"]["error"] = "video_open_failed"
        return result

    frontal_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    alt_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
    )
    profile_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_profileface.xml"
    )

    # CLAHE for contrast enhancement in poor lighting
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0
    max_analysis_seconds = 120.0

    frame_idx = 0
    sampled = 0
    face_frames = 0
    confidence_sum = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if (frame_idx / float(fps)) > max_analysis_seconds:
            break
        sampled += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = clahe.apply(gray)  # enhance contrast
        h, w = gray.shape[:2]
        frame_area = float(h * w) if h and w else 1.0

        detected = False
        best_area = 0.0

        # Try primary frontal cascade (lenient params)
        faces = frontal_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(30, 30),
        )
        if len(faces) > 0:
            detected = True
            best_area = max(float(fw * fh) for (_, _, fw, fh) in faces)

        # Fallback: alt2 cascade (better with glasses, slight angles)
        if not detected:
            faces = alt_cascade.detectMultiScale(
                gray,
                scaleFactor=1.05,
                minNeighbors=3,
                minSize=(30, 30),
            )
            if len(faces) > 0:
                detected = True
                best_area = max(float(fw * fh) for (_, _, fw, fh) in faces)

        # Fallback: profile cascade (side-facing)
        if not detected:
            faces = profile_cascade.detectMultiScale(
                gray,
                scaleFactor=1.05,
                minNeighbors=3,
                minSize=(30, 30),
            )
            if len(faces) > 0:
                detected = True
                best_area = max(float(fw * fh) for (_, _, fw, fh) in faces)

        if detected:
            face_frames += 1
            # Confidence based on face-to-frame area ratio
            # Typical webcam face covers 5-25% of frame
            area_ratio = best_area / frame_area
            conf = _clamp(area_ratio * 5.0, 0.1, 1.0)
            confidence_sum += conf
        frame_idx += 1

    cap.release()

    ratio = _normalize_optional_ratio((face_frames / sampled) if sampled else None)
    avg_conf = (confidence_sum / face_frames) if face_frames > 0 else None
    attention = _normalize_optional_ratio(ratio)
    fatigue = _normalize_optional_ratio((1.0 - ratio) if ratio is not None else None)

    result.update(
        {
            "frames_total": sampled,
            "frames_recognized": face_frames,
            "recognition_ratio": ratio,
            "avg_face_confidence": round(avg_conf, 4) if avg_conf is not None else ratio,
            "attention_score": attention,
            "fatigue_signal": fatigue,
            "metrics_meta": {
                **result["metrics_meta"],
                "frames_total_raw": frame_idx,
            },
        }
    )
    return result


@router.post("/sessions/{session_id}/face-scan", response_model=FaceScanOut)
async def submit_face_scan(
    session_id: UUID,
    payload: FaceScanSubmitIn,
    db: AsyncSession = Depends(get_db),
) -> FaceScanOut:
    r = await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    mm = (payload.missing_metrics.model_dump(exclude_none=True) if payload.missing_metrics else {})
    fs = (payload.face_stats.model_dump(exclude_none=True) if payload.face_stats else {})

    frames_total = payload.frames_total
    if frames_total is None and fs.get("sampled_frames") is not None:
        frames_total = int(fs["sampled_frames"])

    frames_recognized = payload.frames_recognized
    if frames_recognized is None and fs.get("face_frames") is not None:
        frames_recognized = int(fs["face_frames"])

    recognition_ratio = _to_float(payload.recognition_ratio)
    if recognition_ratio is None and fs.get("face_presence_ratio") is not None:
        recognition_ratio = _to_float(fs.get("face_presence_ratio"))
    recognition_ratio = _normalize_optional_ratio(recognition_ratio)

    # Normalize to 0-1 for DB consistency.
    attention_score = _normalize_optional_ratio(payload.attention_score)
    fatigue_signal = _normalize_optional_ratio(payload.fatigue_signal)

    if attention_score is None and mm.get("focus_percentage") is not None:
        focus_pct = _to_float(mm.get("focus_percentage"))
        if focus_pct is not None:
            attention_score = _clamp(focus_pct / 100.0, 0.0, 1.0)

    if fatigue_signal is None and mm.get("fatigue_percentage") is not None:
        fatigue_pct = _to_float(mm.get("fatigue_percentage"))
        if fatigue_pct is not None:
            fatigue_signal = _clamp(fatigue_pct / 100.0, 0.0, 1.0)

    row = FaceScanMetricRow(
        session_id=session_id,
        scanned_at=payload.scanned_at or datetime.now(timezone.utc),
        duration_seconds=payload.duration_seconds,
        frames_total=frames_total,
        frames_recognized=frames_recognized,
        recognition_ratio=recognition_ratio,
        avg_face_confidence=_to_float(payload.avg_face_confidence),
        attention_score=attention_score,
        fatigue_signal=fatigue_signal,
        status=payload.status,
        error_message=payload.error_message,
        metrics_meta={
            **payload.metrics_meta,
            "missing_metrics": mm,
            "face_stats": fs,
            "rppg": payload.rppg or {},
        },
    )

    print("\n[INFO] Face scan payload before DB insert:\n")
    print(
        json.dumps(
            {
                "session_id": str(session_id),
                "scanned_at": row.scanned_at.isoformat(),
                "duration_seconds": row.duration_seconds,
                "frames_total": row.frames_total,
                "frames_recognized": row.frames_recognized,
                "recognition_ratio": row.recognition_ratio,
                "avg_face_confidence": row.avg_face_confidence,
                "attention_score": row.attention_score,
                "fatigue_signal": row.fatigue_signal,
                "status": row.status,
                "error_message": row.error_message,
                "metrics_meta": row.metrics_meta,
            },
            indent=2,
            default=str,
        )
    )

    db.add(row)
    await db.commit()
    await db.refresh(row)

    return FaceScanOut(
        id=row.id,
        session_id=str(row.session_id),
        scanned_at=row.scanned_at.isoformat(),
        status=row.status,
        recognition_ratio=row.recognition_ratio,
        attention_score=row.attention_score,
        fatigue_signal=row.fatigue_signal,
        missing_metrics=mm,
    )


@router.get("/sessions/{session_id}/face-scan/latest", response_model=FaceScanOut)
async def latest_face_scan(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> FaceScanOut:
    r = await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    res = await db.execute(
        select(FaceScanMetricRow)
        .where(FaceScanMetricRow.session_id == session_id)
        .order_by(FaceScanMetricRow.scanned_at.desc(), FaceScanMetricRow.id.desc())
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="face_scan_not_found")

    mm = (row.metrics_meta or {}).get("missing_metrics") or {}
    return FaceScanOut(
        id=row.id,
        session_id=str(row.session_id),
        scanned_at=row.scanned_at.isoformat(),
        status=row.status,
        recognition_ratio=row.recognition_ratio,
        attention_score=row.attention_score,
        fatigue_signal=row.fatigue_signal,
        missing_metrics=mm,
    )


@router.post("/sessions/{session_id}/face-scan/video", response_model=FaceScanOut)
async def submit_face_scan_video(
    session_id: UUID,
    video: UploadFile = File(...),
    scanned_at: datetime | None = Form(default=None),
    duration_seconds: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> FaceScanOut:
    r = await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    suffix = ".webm"
    if video.filename and "." in video.filename:
        suffix = os.path.splitext(video.filename)[1] or suffix

    temp_path = ""
    status = "ok"
    error_message = None
    analyzed = {
        "frames_total": None,
        "frames_recognized": None,
        "recognition_ratio": None,
        "avg_face_confidence": None,
        "attention_score": None,
        "fatigue_signal": None,
        "metrics_meta": {},
    }

    try:
        raw = await video.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(raw)
            temp_path = temp_file.name

        analyzed = _analyze_video_sample(temp_path)
        if analyzed.get("metrics_meta", {}).get("error"):
            status = "partial"
            error_message = analyzed["metrics_meta"]["error"]
    except Exception as exc:
        status = "error"
        error_message = str(exc)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    row = FaceScanMetricRow(
        session_id=session_id,
        scanned_at=scanned_at or datetime.now(timezone.utc),
        duration_seconds=duration_seconds,
        frames_total=analyzed.get("frames_total"),
        frames_recognized=analyzed.get("frames_recognized"),
        recognition_ratio=analyzed.get("recognition_ratio"),
        avg_face_confidence=analyzed.get("avg_face_confidence"),
        attention_score=analyzed.get("attention_score"),
        fatigue_signal=analyzed.get("fatigue_signal"),
        status=status,
        error_message=error_message,
        metrics_meta={
            "upload": {
                "filename": video.filename,
                "content_type": video.content_type,
            },
            **(analyzed.get("metrics_meta") or {}),
        },
    )

    print("\n[INFO] Face scan video result before DB insert:\n")
    print(
        json.dumps(
            {
                "session_id": str(session_id),
                "scanned_at": row.scanned_at.isoformat(),
                "duration_seconds": row.duration_seconds,
                "frames_total": row.frames_total,
                "frames_recognized": row.frames_recognized,
                "recognition_ratio": row.recognition_ratio,
                "avg_face_confidence": row.avg_face_confidence,
                "attention_score": row.attention_score,
                "fatigue_signal": row.fatigue_signal,
                "status": row.status,
                "error_message": row.error_message,
                "metrics_meta": row.metrics_meta,
            },
            indent=2,
            default=str,
        )
    )

    db.add(row)
    await db.commit()
    await db.refresh(row)

    return FaceScanOut(
        id=row.id,
        session_id=str(row.session_id),
        scanned_at=row.scanned_at.isoformat(),
        status=row.status,
        recognition_ratio=row.recognition_ratio,
        attention_score=row.attention_score,
        fatigue_signal=row.fatigue_signal,
        missing_metrics=((row.metrics_meta or {}).get("missing_metrics") or {}),
    )

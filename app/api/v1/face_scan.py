from datetime import datetime, timezone
import os
import tempfile
from uuid import UUID
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DashboardMetricRow, FaceScanMetricRow, SessionRow, get_db
from app.schemas.face_scan import FaceScanOut, FaceScanSubmitIn
from app.services.buffer import floor_to_minute_utc

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


def _normalize_optional_percent(value):
    val = _to_float(value)
    if val is None:
        return None
    if val <= 1.0:
        val = val * 100.0
    return _clamp(val, 0.0, 100.0)


def _label_from_percentages(
    focus_percentage: float | None,
    fatigue_percentage: float | None,
    confusion_percentage: float | None,
) -> str:
    pairs = [
        ("focus", focus_percentage if focus_percentage is not None else -1.0),
        ("fatigue", fatigue_percentage if fatigue_percentage is not None else -1.0),
        ("confused", confusion_percentage if confusion_percentage is not None else -1.0),
    ]
    winner = max(pairs, key=lambda item: item[1])
    return winner[0] if winner[1] >= 0 else "idle"


def _derive_missing_metrics_from_video(
    *,
    attention_score: float | None,
    fatigue_signal: float | None,
    recognition_ratio: float | None = None,
    avg_face_confidence: float | None = None,
    rppg: dict | None = None,
) -> dict:
    att = _normalize_optional_ratio(attention_score)
    fat = _normalize_optional_ratio(fatigue_signal)

    focus_pct = _clamp(((att if att is not None else 0.55) * 100.0), 0.0, 100.0)
    fatigue_pct = _clamp(((fat if fat is not None else 0.30) * 100.0), 0.0, 100.0)
    confusion_pct = _clamp(100.0 - ((0.6 * focus_pct) + (0.2 * fatigue_pct)), 0.0, 100.0)

    stress_ratio = _clamp((fat if fat is not None else 0.30), 0.0, 1.0)
    productivity = _clamp((focus_pct * 0.75) + ((100.0 - fatigue_pct) * 0.25), 0.0, 100.0)

    # Use real rPPG vitals when available, fallback to estimation
    rppg = rppg or {}
    hr = rppg.get("rppg_hr_bpm")
    hrv = rppg.get("rppg_hrv_ms")
    spo2 = rppg.get("rppg_spo2")

    # Fallback: estimate if rPPG couldn't determine
    if hr is None:
        _f = fat if fat is not None else 0.2
        rec = _normalize_optional_ratio(recognition_ratio) if recognition_ratio is not None else 0.5
        conf = _normalize_optional_ratio(avg_face_confidence) if avg_face_confidence is not None else 0.5
        _var = (conf * 3.0) + (rec * 2.0)
        hr = round(68.0 + _f * 27.0 + (1.0 - (att or 0.5)) * 8.0 + _var, 1)

    if hrv is None:
        hrv = round(55.0 - stress_ratio * 25.0, 1)

    if spo2 is None:
        spo2 = round(98.0 - stress_ratio * 2.0, 1)

    return {
        "avg_heart_rate": _clamp(hr, 42.0, 210.0),
        "avg_hrv": _clamp(hrv, 5.0, 200.0),
        "avg_spo2": _clamp(spo2, 90.0, 100.0),
        "stress_index": round(stress_ratio, 4),
        "fatigue_index": round(stress_ratio, 4),
        "focus_percentage": round(focus_pct, 2),
        "fatigue_percentage": round(fatigue_pct, 2),
        "confusion_percentage": round(confusion_pct, 2),
        "productivity_score": round(productivity, 2),
        "rppg_source": "measured" if rppg.get("rppg_hr_bpm") is not None else "estimated",
        "rppg_signal_quality": rppg.get("rppg_signal_quality"),
    }


async def _upsert_dashboard_from_face_scan(
    db: AsyncSession,
    session_id: UUID,
    scanned_at: datetime,
    *,
    avg_heart_rate: float | None,
    avg_hrv: float | None,
    avg_spo2: float | None,
    stress_index: float | None,
    fatigue_index: float | None,
    focus_percentage: float | None,
    fatigue_percentage: float | None,
    confusion_percentage: float | None,
    productivity_score: float | None,
    source: str,
) -> None:
    bucket_start = floor_to_minute_utc(scanned_at)
    state_label = _label_from_percentages(focus_percentage, fatigue_percentage, confusion_percentage)

    values = {
        "session_id": session_id,
        "bucket_start": bucket_start,
        "avg_heart_rate": avg_heart_rate,
        "avg_hrv": avg_hrv,
        "avg_spo2": avg_spo2,
        "stress_index": stress_index,
        "fatigue_index": fatigue_index,
        "focus_percentage": focus_percentage,
        "fatigue_percentage": fatigue_percentage,
        "confusion_percentage": confusion_percentage,
        "productivity_score": productivity_score,
        "state_label": state_label,
        "model_version": "face-scan-v1",
        "feature_meta": {"source": source, "scanned_at": scanned_at.isoformat()},
    }

    insert_stmt = pg_insert(DashboardMetricRow).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_dashboard_session_bucket",
        set_={
            "avg_heart_rate": insert_stmt.excluded.avg_heart_rate,
            "avg_hrv": insert_stmt.excluded.avg_hrv,
            "avg_spo2": insert_stmt.excluded.avg_spo2,
            "stress_index": insert_stmt.excluded.stress_index,
            "fatigue_index": insert_stmt.excluded.fatigue_index,
            "focus_percentage": insert_stmt.excluded.focus_percentage,
            "fatigue_percentage": insert_stmt.excluded.fatigue_percentage,
            "confusion_percentage": insert_stmt.excluded.confusion_percentage,
            "productivity_score": insert_stmt.excluded.productivity_score,
            "state_label": insert_stmt.excluded.state_label,
            "model_version": insert_stmt.excluded.model_version,
            "feature_meta": insert_stmt.excluded.feature_meta,
        },
    )
    await db.execute(upsert_stmt)


def _estimate_rppg_vitals(
    green_signal: list[float],
    red_signal: list[float],
    blue_signal: list[float],
    fps: float,
) -> dict:
    """
    Estimate heart rate, HRV, and SpO2 from face ROI color channel signals
    using remote photoplethysmography (rPPG).

    Method:
    - Green channel → bandpass filter (0.7–3.5 Hz) → FFT → dominant freq → HR
    - Peak detection on filtered signal → inter-beat intervals → RMSSD (HRV)
    - Red/Blue ratio → simplified SpO2 approximation
    """
    import numpy as np
    from scipy.signal import butter, filtfilt, find_peaks, detrend

    result = {
        "rppg_hr_bpm": None,
        "rppg_hrv_ms": None,
        "rppg_spo2": None,
        "rppg_signal_quality": None,
        "rppg_method": "green-channel-fft",
    }

    if len(green_signal) < 30:  # need at least ~6 seconds at 5 FPS
        result["rppg_signal_quality"] = 0.0
        return result

    try:
        sig = np.array(green_signal, dtype=np.float64)

        # 1. Detrend (remove slow drift from lighting changes)
        sig = detrend(sig)

        # 2. Normalize
        sig_std = np.std(sig)
        if sig_std < 1e-6:
            result["rppg_signal_quality"] = 0.0
            return result
        sig = (sig - np.mean(sig)) / sig_std

        # 3. Bandpass filter: 0.7–3.5 Hz (42–210 BPM)
        nyquist = fps / 2.0
        low_hz = 0.7
        high_hz = min(3.5, nyquist * 0.95)  # stay below Nyquist

        if high_hz <= low_hz:
            # FPS too low for proper filtering
            result["rppg_signal_quality"] = 0.1
            return result

        b, a = butter(3, [low_hz / nyquist, high_hz / nyquist], btype="band")
        filtered = filtfilt(b, a, sig)

        # 4. FFT to find dominant frequency
        n = len(filtered)
        fft_vals = np.fft.rfft(filtered)
        fft_mag = np.abs(fft_vals)
        freqs = np.fft.rfftfreq(n, d=1.0 / fps)

        # Only look in the heart rate range (0.7–3.5 Hz)
        mask = (freqs >= low_hz) & (freqs <= high_hz)
        if not np.any(mask):
            result["rppg_signal_quality"] = 0.1
            return result

        masked_mag = fft_mag[mask]
        masked_freqs = freqs[mask]

        dominant_idx = np.argmax(masked_mag)
        dominant_freq = masked_freqs[dominant_idx]
        hr_bpm = round(float(dominant_freq * 60.0), 1)

        # Signal quality: ratio of peak power to total power in band
        peak_power = masked_mag[dominant_idx] ** 2
        total_power = np.sum(masked_mag ** 2)
        signal_quality = round(float(peak_power / total_power) if total_power > 0 else 0.0, 4)

        result["rppg_hr_bpm"] = _clamp(hr_bpm, 42.0, 210.0)
        result["rppg_signal_quality"] = signal_quality

        # 5. HRV estimation from peak-to-peak intervals
        # Find peaks in filtered signal
        min_distance = max(1, int(fps * 0.4))  # at least 0.4s between beats
        peaks, _ = find_peaks(filtered, distance=min_distance, prominence=0.1)

        if len(peaks) >= 3:
            # Inter-beat intervals in milliseconds
            ibi_samples = np.diff(peaks)
            ibi_ms = (ibi_samples / fps) * 1000.0

            # Filter out physiologically impossible intervals (<300ms or >1500ms)
            valid_ibi = ibi_ms[(ibi_ms >= 300) & (ibi_ms <= 1500)]

            if len(valid_ibi) >= 2:
                # RMSSD (root mean square of successive differences)
                successive_diffs = np.diff(valid_ibi)
                rmssd = float(np.sqrt(np.mean(successive_diffs ** 2)))
                result["rppg_hrv_ms"] = round(_clamp(rmssd, 5.0, 200.0), 1)

        # 6. SpO2 approximation from red/blue channel ratio
        if len(red_signal) >= 30 and len(blue_signal) >= 30:
            red_arr = np.array(red_signal, dtype=np.float64)
            blue_arr = np.array(blue_signal, dtype=np.float64)

            red_mean = np.mean(red_arr)
            blue_mean = np.mean(blue_arr)

            if red_mean > 0 and blue_mean > 0:
                red_ac = np.std(red_arr) / red_mean
                blue_ac = np.std(blue_arr) / blue_mean

                if blue_ac > 1e-6:
                    # Simplified ratio of ratios
                    r_ratio = red_ac / blue_ac
                    # Empirical SpO2 approximation (calibrated for webcam)
                    # Normal range: 94-100%
                    spo2_est = 110.0 - 25.0 * r_ratio
                    result["rppg_spo2"] = round(_clamp(spo2_est, 90.0, 100.0), 1)

    except Exception as e:
        result["rppg_signal_quality"] = 0.0
        result["rppg_method"] = f"error: {str(e)[:80]}"

    return result


def _analyze_video_sample(video_path: str) -> dict:
    result = {
        "frames_total": None,
        "frames_recognized": None,
        "recognition_ratio": None,
        "avg_face_confidence": None,
        "attention_score": None,
        "fatigue_signal": None,
        "rppg": {},
        "metrics_meta": {
            "analyzer": "opencv-haar-rppg",
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

    # rPPG signal buffers: collect mean color channel values from face ROI
    green_signal = []
    red_signal = []
    blue_signal = []

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
        best_face = None  # (x, y, fw, fh) of largest face

        # Try primary frontal cascade (lenient params)
        faces = frontal_cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=3,
            minSize=(30, 30),
        )
        if len(faces) > 0:
            detected = True
            for (fx, fy, fw, fh) in faces:
                area = float(fw * fh)
                if area > best_area:
                    best_area = area
                    best_face = (fx, fy, fw, fh)

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
                for (fx, fy, fw, fh) in faces:
                    area = float(fw * fh)
                    if area > best_area:
                        best_area = area
                        best_face = (fx, fy, fw, fh)

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
                for (fx, fy, fw, fh) in faces:
                    area = float(fw * fh)
                    if area > best_area:
                        best_area = area
                        best_face = (fx, fy, fw, fh)

        if detected and best_face is not None:
            face_frames += 1
            area_ratio = best_area / frame_area
            conf = _clamp(area_ratio * 5.0, 0.1, 1.0)
            confidence_sum += conf

            # --- rPPG: extract color channels from forehead region ---
            # Forehead = top 60% of face, middle 60% width (most skin, least hair)
            fx, fy, fw, fh = best_face
            roi_x = fx + int(fw * 0.2)
            roi_y = fy + int(fh * 0.05)
            roi_w = int(fw * 0.6)
            roi_h = int(fh * 0.55)

            # Clamp to frame bounds
            roi_x = max(0, roi_x)
            roi_y = max(0, roi_y)
            roi_x2 = min(w, roi_x + roi_w)
            roi_y2 = min(h, roi_y + roi_h)

            if roi_x2 > roi_x and roi_y2 > roi_y:
                roi = frame[roi_y:roi_y2, roi_x:roi_x2]
                # BGR channel means
                b_mean = float(roi[:, :, 0].mean())
                g_mean = float(roi[:, :, 1].mean())
                r_mean = float(roi[:, :, 2].mean())
                green_signal.append(g_mean)
                red_signal.append(r_mean)
                blue_signal.append(b_mean)
        frame_idx += 1

    cap.release()

    ratio = _normalize_optional_ratio((face_frames / sampled) if sampled else None)
    avg_conf = (confidence_sum / face_frames) if face_frames > 0 else None
    attention = _normalize_optional_ratio(ratio)
    fatigue = _normalize_optional_ratio((1.0 - ratio) if ratio is not None else None)

    # --- rPPG vital sign estimation ---
    rppg_result = _estimate_rppg_vitals(green_signal, red_signal, blue_signal, fps)

    result.update(
        {
            "frames_total": sampled,
            "frames_recognized": face_frames,
            "recognition_ratio": ratio,
            "avg_face_confidence": round(avg_conf, 4) if avg_conf is not None else ratio,
            "attention_score": attention,
            "fatigue_signal": fatigue,
            "rppg": rppg_result,
            "metrics_meta": {
                **result["metrics_meta"],
                "frames_total_raw": frame_idx,
                "rppg_frames_used": len(green_signal),
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

    await _upsert_dashboard_from_face_scan(
        db,
        session_id=session_id,
        scanned_at=row.scanned_at,
        avg_heart_rate=_to_float(mm.get("avg_heart_rate")),
        avg_hrv=_to_float(mm.get("avg_hrv")),
        avg_spo2=_to_float(mm.get("avg_spo2")),
        stress_index=_normalize_optional_ratio(mm.get("stress_index")),
        fatigue_index=_normalize_optional_ratio(mm.get("fatigue_index")),
        focus_percentage=_normalize_optional_percent(mm.get("focus_percentage")) or (
            (_normalize_optional_ratio(row.attention_score) * 100.0) if row.attention_score is not None else None
        ),
        fatigue_percentage=_normalize_optional_percent(mm.get("fatigue_percentage")) or (
            (_normalize_optional_ratio(row.fatigue_signal) * 100.0) if row.fatigue_signal is not None else None
        ),
        confusion_percentage=_normalize_optional_percent(mm.get("confusion_percentage")),
        productivity_score=_normalize_optional_percent(mm.get("productivity_score")),
        source="face-scan-json",
    )

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
            "missing_metrics": _derive_missing_metrics_from_video(
                attention_score=analyzed.get("attention_score"),
                fatigue_signal=analyzed.get("fatigue_signal"),
                recognition_ratio=analyzed.get("recognition_ratio"),
                avg_face_confidence=analyzed.get("avg_face_confidence"),
                rppg=analyzed.get("rppg"),
            ),
            **(analyzed.get("metrics_meta") or {}),
        },
    )

    mm_video = (row.metrics_meta or {}).get("missing_metrics") or {}

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

    await _upsert_dashboard_from_face_scan(
        db,
        session_id=session_id,
        scanned_at=row.scanned_at,
        avg_heart_rate=_to_float(mm_video.get("avg_heart_rate")),
        avg_hrv=_to_float(mm_video.get("avg_hrv")),
        avg_spo2=_to_float(mm_video.get("avg_spo2")),
        stress_index=_normalize_optional_ratio(mm_video.get("stress_index")),
        fatigue_index=_normalize_optional_ratio(mm_video.get("fatigue_index")),
        focus_percentage=(
            _normalize_optional_percent(mm_video.get("focus_percentage"))
            or ((_normalize_optional_ratio(row.attention_score) * 100.0) if row.attention_score is not None else None)
        ),
        fatigue_percentage=(
            _normalize_optional_percent(mm_video.get("fatigue_percentage"))
            or ((_normalize_optional_ratio(row.fatigue_signal) * 100.0) if row.fatigue_signal is not None else None)
        ),
        confusion_percentage=_normalize_optional_percent(mm_video.get("confusion_percentage")),
        productivity_score=(
            _normalize_optional_percent(mm_video.get("productivity_score"))
            or ((_normalize_optional_ratio(row.attention_score) * 100.0) if row.attention_score is not None else None)
        ),
        source="face-scan-video",
    )

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

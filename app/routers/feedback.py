import uuid

from fastapi import APIRouter, HTTPException

from app.dependencies import DbDep
from app.models import CognitiveStatePrediction, FeedbackLog, SessionRecord
from app.schemas.feedback import FeedbackCreate, FeedbackOut

router = APIRouter(prefix="/sessions", tags=["feedback"])


@router.post("/{session_id}/feedback", response_model=FeedbackOut)
async def create_feedback(
    session_id: uuid.UUID,
    body: FeedbackCreate,
    db: DbDep,
) -> FeedbackLog:
    sess = await db.get(SessionRecord, session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.prediction_id is not None:
        pred = await db.get(CognitiveStatePrediction, body.prediction_id)
        if not pred or pred.session_id != session_id:
            raise HTTPException(status_code=404, detail="Prediction not found for session")

    row = FeedbackLog(
        session_id=session_id,
        prediction_id=body.prediction_id,
        feedback_type=body.feedback_type,
        label=body.label,
        rating=body.rating,
        comment=body.comment,
        context=body.context,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row

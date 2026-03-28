import uuid

from fastapi import APIRouter

from app.dependencies import DbDep, RedisDep
from app.schemas.snapshot import SnapshotIngest, SnapshotIngestResponse
from app.services.ingest import ingest_snapshot

router = APIRouter(prefix="/sessions", tags=["snapshots"])


@router.post("/{session_id}/snapshots", response_model=SnapshotIngestResponse)
async def create_snapshot(
    session_id: uuid.UUID,
    body: SnapshotIngest,
    db: DbDep,
    redis_client: RedisDep,
) -> SnapshotIngestResponse:
    result = await ingest_snapshot(db, redis_client, session_id, body)
    await db.commit()
    return result

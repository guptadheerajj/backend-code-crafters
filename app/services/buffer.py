import json
import uuid
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from app.config import settings


def _key(session_id: uuid.UUID) -> str:
    return f"snapbuf:{session_id}"


def _lock_key(session_id: uuid.UUID) -> str:
    return f"predlock:{session_id}"


async def push_snapshot(
    client: redis.Redis,
    session_id: uuid.UUID,
    record: dict[str, Any],
) -> None:
    """Store newest snapshot at head; keep last N entries."""
    key = _key(session_id)
    body = json.dumps(record, default=_json_default)
    async with client.pipeline(transaction=True) as pipe:
        pipe.lpush(key, body)
        pipe.ltrim(key, 0, settings.snapshot_buffer_size - 1)
        pipe.expire(key, settings.redis_buffer_ttl_sec)
        await pipe.execute()


async def list_snapshots(
    client: redis.Redis,
    session_id: uuid.UUID,
) -> list[dict[str, Any]]:
    key = _key(session_id)
    raw = await client.lrange(key, 0, settings.snapshot_buffer_size - 1)
    out: list[dict[str, Any]] = []
    for item in raw:
        out.append(json.loads(item))
    return out


async def acquire_prediction_lock(client: redis.Redis, session_id: uuid.UUID) -> bool:
    key = _lock_key(session_id)
    ok = await client.set(key, "1", nx=True, ex=settings.prediction_lock_ttl_sec)
    return bool(ok)


def _json_default(o: object) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError

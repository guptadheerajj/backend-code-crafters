from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db


def create_redis() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis_client(request: Request) -> AsyncGenerator[redis.Redis, None]:
    client: redis.Redis = request.app.state.redis
    yield client


DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]

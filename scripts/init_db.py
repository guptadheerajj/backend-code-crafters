"""Create all tables from SQLAlchemy models (matches app/db.py).

Run from repo root after DATABASE_URL in .env points at the new database:

  python scripts/init_db.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Base, engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("OK:", sorted(Base.metadata.tables.keys()))


if __name__ == "__main__":
    asyncio.run(main())

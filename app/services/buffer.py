"""Rolling per-session snapshot buffer (memory). Swap for Redis via same interface."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from uuid import UUID

from app.schemas.snapshot import NormalizedSnapshot

# type aliases for optional Redis backend
SessionId = UUID


@dataclass
class SessionBufferState:
    snapshots: deque[NormalizedSnapshot] = field(default_factory=lambda: deque(maxlen=6))
    last_bucket_processed: datetime | None = None


class SnapshotBuffer:
    """
    Keeps last N snapshots (default 6 ≈ 3 minutes at 30s cadence).
    Not persisted — aligns with “no long-term raw storage”.
    """

    def __init__(self, maxlen: int = 6) -> None:
        self._maxlen = maxlen
        self._by_session: dict[SessionId, SessionBufferState] = {}
        self._lock = Lock()

    def append(self, session_id: SessionId, snap: NormalizedSnapshot) -> None:
        with self._lock:
            state = self._by_session.setdefault(
                session_id, SessionBufferState(snapshots=deque(maxlen=self._maxlen))
            )
            state.snapshots.append(snap)

    def window(self, session_id: SessionId) -> list[NormalizedSnapshot]:
        with self._lock:
            st = self._by_session.get(session_id)
            if not st:
                return []
            return list(st.snapshots)

    def get_last_bucket(self, session_id: SessionId) -> datetime | None:
        with self._lock:
            st = self._by_session.get(session_id)
            return st.last_bucket_processed if st else None

    def set_last_bucket(self, session_id: SessionId, bucket_start: datetime) -> None:
        with self._lock:
            st = self._by_session.setdefault(
                session_id, SessionBufferState(snapshots=deque(maxlen=self._maxlen))
            )
            st.last_bucket_processed = bucket_start

    def drop_session(self, session_id: SessionId) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)


def floor_to_minute_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.replace(second=0, microsecond=0)

from app.config import get_settings
from app.services.buffer import SnapshotBuffer

snapshot_buffer = SnapshotBuffer(maxlen=6)


def get_snapshot_buffer() -> SnapshotBuffer:
    return snapshot_buffer


def get_settings_dep():
    return get_settings()

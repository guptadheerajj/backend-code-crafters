# Cognitive State API

Backend API for cognitive state detection from session snapshots.

It provides:
- session lifecycle management
- snapshot ingestion with idempotency protection
- rolling Redis window buffer
- rule-based prediction stub (easy to replace with a real model)
- session summary aggregation
- feedback logging
- timeline and dashboard-style query endpoints

## Table of contents

- Overview
- Architecture
- Tech stack
- Requirements
- Project structure
- Quick start (Windows PowerShell)
- Environment variables
- Database migrations
- Run the API
- API endpoints
- Example requests
- Behavior notes
- Development
- Troubleshooting
- Next improvements

## Overview

The service accepts periodic client snapshots for a session, updates online summary stats, stores recent snapshots in Redis, and triggers inference when enough data is available.

Prediction currently uses deterministic rules in `app/services/inference.py`.

## Architecture

1. Client starts a session.
2. Client posts snapshots to the session.
3. API stores each snapshot in PostgreSQL.
4. API updates session summary aggregates.
5. API pushes snapshot to Redis list buffer.
6. When buffer reaches configured size (default 10), prediction is computed.
7. Prediction is persisted and latest state is written to session record.
8. Client can query summary, predictions, timeline, and post feedback.

## Tech stack

- Python 3.11+
- FastAPI
- SQLAlchemy async + asyncpg
- Alembic (async migrations)
- Redis (async client)
- Pydantic settings

## Requirements

- Python 3.11 or newer
- PostgreSQL running and reachable
- Redis running and reachable

## Project structure

- `app/main.py`: FastAPI app, lifespan Redis initialization, `/health`
- `app/config.py`: settings and defaults (`DATABASE_URL`, `REDIS_URL`, model settings)
- `app/db.py`: async SQLAlchemy engine and session
- `app/dependencies.py`: DB and Redis dependencies
- `app/models/`: SQLAlchemy models
- `app/schemas/`: Pydantic request/response schemas
- `app/routers/`: route handlers
- `app/services/`: ingest pipeline, buffer, temporal features, summary merge, inference
- `alembic/`: migration environment and versions

## Quick start (Windows PowerShell)

From repository root:

```powershell
Set-Location C:\Users\nitin\cognitive-state-api
```

Create and use a Python 3.11 virtual environment:

```powershell
py -3.11 -m venv .venv311
.\.venv311\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

Set runtime environment variables:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://nitin:1234@localhost:5432/cognitive_db"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:PYTHONPATH = "$PWD"
```

Run migrations:

```powershell
alembic upgrade head
```

Start API:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify:

- Health: http://127.0.0.1:8000/health
- Docs: http://127.0.0.1:8000/docs

## Environment variables

`app/config.py` loads from `.env` if present and also supports shell env vars.

Example `.env`:

```env
DATABASE_URL=postgresql+asyncpg://nitin:1234@localhost:5432/cognitive_db
REDIS_URL=redis://localhost:6379/0
SNAPSHOT_BUFFER_SIZE=10
REDIS_BUFFER_TTL_SEC=86400
PREDICTION_LOCK_TTL_SEC=5
MODEL_NAME=rules_stub
MODEL_VERSION=0.1.0
DEBUG=false
APP_NAME=cognitive-state-api
```

Notes:
- Shell env vars override `.env` values.
- `DATABASE_URL` must use async driver format `postgresql+asyncpg://...`.

## Database migrations

Apply latest migration:

```powershell
alembic upgrade head
```

Migration files are in `alembic/versions/`.

## Run the API

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

If import errors occur, ensure `PYTHONPATH` is set to repo root in the same shell.

## API endpoints

Base prefix: `/api/v1`

### Sessions
- `POST /api/v1/sessions` start session
- `GET /api/v1/sessions/{session_id}` get session
- `POST /api/v1/sessions/{session_id}/end` end/abandon session

### Snapshots
- `POST /api/v1/sessions/{session_id}/snapshots` ingest snapshot, update summary/buffer, optional prediction

### Users
- `GET /api/v1/users/{user_id}/sessions` list user sessions

### Dashboard
- `GET /api/v1/sessions/{session_id}/summary`
- `GET /api/v1/sessions/{session_id}/predictions`
- `GET /api/v1/sessions/{session_id}/timeline`

### Feedback
- `POST /api/v1/sessions/{session_id}/feedback`

### Health
- `GET /health`

## Example requests

### 1) Start a session

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/sessions" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "11111111-1111-1111-1111-111111111111",
    "device_id": "dev-win-01",
    "external_ref": "ext-123",
    "meta": {"source": "manual-test"}
  }'
```

### 2) Ingest a snapshot

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/sessions/{session_id}/snapshots" \
  -H "Content-Type: application/json" \
  -d '{
    "captured_at": "2026-03-28T12:00:00Z",
    "client_seq": 1,
    "idempotency_key": "seq-1",
    "payload": {
      "keyboard": {"wpm": 45},
      "camera": {"hr_bpm": 72},
      "derived": {"stress_index": 0.45, "fatigue_index": 0.35},
      "tab": {"switches": 2}
    }
  }'
```

### 3) End session

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/sessions/{session_id}/end" \
  -H "Content-Type: application/json" \
  -d '{"status": "ended"}'
```

## Behavior notes

- Idempotency:
  - Duplicate `client_seq` or `idempotency_key` returns existing snapshot row.
  - Summary and Redis buffer are not updated for duplicates.
- Prediction trigger:
  - Runs when Redis window reaches `SNAPSHOT_BUFFER_SIZE` (default 10).
  - Uses short lock key `predlock:{session_id}` to avoid concurrent duplicate inference.
- Session record updates:
  - `latest_state_label` and `latest_state_at` are updated when prediction is written.
- Inference implementation:
  - Current logic is rule stub in `app/services/inference.py`.
  - Replace with ONNX or model service call as needed.

## Development

Install development extras:

```powershell
pip install -e .[dev]
```

Optional lint:

```powershell
ruff check .
```

## Troubleshooting

- `requires a different Python: 3.9 not in >=3.11`
  - Use Python 3.11+ and recreate venv.
- `No module named alembic.__main__`
  - Run `alembic upgrade head` (CLI), not `python -m alembic`.
- DB connection errors
  - Verify username/password/db in `DATABASE_URL`.
  - Ensure database exists and Postgres service is running.
- Redis connection errors
  - Ensure Redis is up on configured host/port.

## Next improvements

- Authentication and authorization (JWT or API keys)
- Dedicated snapshot fetch endpoint (`GET /snapshots/{id}`)
- Async queue/worker for inference
- Integration tests and load tests

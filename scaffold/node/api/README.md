# Custom API Endpoints

Drop Python files in this folder to add endpoints to the report worker.

Any `.py` file with a `router` attribute (a FastAPI `APIRouter`) is auto-mounted
at startup. No configuration needed — just create the file and redeploy.

## Quick start

```python
# api/my_endpoints.py
from fastapi import APIRouter

router = APIRouter(prefix="/custom", tags=["custom"])

@router.get("/hello")
def hello():
    return {"message": "Hello from custom endpoint"}
```

After `make deploy`, this endpoint is available at `http://localhost:8000/custom/hello`.

## Using the database

Import the same dependencies used by built-in endpoints:

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlmodel import Session
from coordinator_node.db import create_session, DBModelRepository

router = APIRouter(prefix="/custom", tags=["custom"])

def get_db_session():
    with create_session() as session:
        yield session

@router.get("/models/count")
def model_count(session: Annotated[Session, Depends(get_db_session)]):
    models = DBModelRepository(session).fetch_all()
    return {"count": len(models)}
```

## Using metrics and ensemble data

```python
from fastapi import APIRouter
from coordinator_node.metrics import get_default_registry

router = APIRouter(prefix="/custom", tags=["custom"])

@router.get("/available-metrics")
def available_metrics():
    return {"metrics": get_default_registry().available()}
```

## Explicit imports (alternative)

Instead of file discovery, set the `API_ROUTES` env var:

```env
API_ROUTES=my_package.routes:router,another_module:custom_router
```

Both mechanisms work together.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `API_ROUTES_DIR` | `api/` | Directory to scan for router files |
| `API_ROUTES` | _(empty)_ | Comma-separated `module:attr` paths for explicit imports |

## Rules

- File names starting with `_` are skipped (e.g. `__init__.py`, `_helpers.py`)
- Each file must expose a `router` attribute that is a `fastapi.APIRouter`
- Use a `prefix` on your router to avoid collisions with built-in `/reports/*` endpoints
- Files are mounted in alphabetical order

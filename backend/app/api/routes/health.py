"""Health-check endpoints.

/health       -- liveness: the process is up.
/health/db    -- readiness: the database is reachable.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import check_database_connection

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/db")
async def health_db() -> JSONResponse:
    connected = await check_database_connection()
    status_code = 200 if connected else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if connected else "unavailable",
            "database": "postgresql",
            "connected": connected,
        },
    )

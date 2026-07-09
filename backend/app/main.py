"""InsightForge API entry point.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, investigate, observability
from app.core.config import get_settings
from app.core.database import check_database_connection, dispose_engine
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("startup")
    connected = await check_database_connection()
    if connected:
        log.info("database_connected")
    else:
        # Don't crash: /health should still respond so the problem is visible.
        log.warning("database_unreachable", hint="is `docker compose up -d postgres` running?")
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="AI-powered autonomous data analyst",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],  # Next.js dev server (later stage)
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(investigate.router)
    app.include_router(observability.router)
    return app


app = create_app()

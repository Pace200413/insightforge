"""Foundation tests: app boots, health endpoints respond, config loads."""

from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app


async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "InsightForge"


async def test_health_db_reports_connection_state():
    """Passes whether or not Postgres is running: 200 connected, 503 not."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/db")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "connected" in body


def test_settings_build_database_url():
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.postgres_db in settings.database_url

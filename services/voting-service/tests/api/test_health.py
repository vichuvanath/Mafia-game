"""Basic health check API test."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_check() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "voting-service"}


@pytest.mark.asyncio
async def test_ready_check() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "components" in data


@pytest.mark.asyncio
async def test_request_id_header_and_error_format() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/non-existent-endpoint")
        assert response.status_code == 404
        assert "X-Request-ID" in response.headers
        data = response.json()
        assert data["code"] == "NOT_FOUND"
        assert "message" in data
        assert "request_id" in data
        assert data["request_id"] == response.headers["X-Request-ID"]

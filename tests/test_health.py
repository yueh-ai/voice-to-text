"""
Health endpoint tests.

Contract:
- GET /v1/health returns status and version information
"""

import pytest


async def test_health_returns_ok_status(client):
    """Health endpoint should return status 'ok'."""
    response = await client.get("/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


async def test_health_includes_version(client):
    """Health endpoint should include version information."""
    response = await client.get("/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert isinstance(data["version"], str)

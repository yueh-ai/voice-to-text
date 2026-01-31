"""Tests for session inspection API endpoints.

These tests verify the session API behavior through HTTP endpoints.
"""

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    """Sync test client that triggers app lifespan."""
    from transcription_service.main import app

    with TestClient(app) as client:
        yield client


class TestSessionListEndpoint:
    """Test GET /v1/sessions endpoint."""

    def test_list_sessions_returns_empty_initially(self, client):
        """List sessions should return empty list when no sessions active."""
        response = client.get("/v1/sessions")

        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "count" in data

    def test_list_sessions_returns_active_sessions(self, client):
        """List sessions should include active WebSocket sessions."""
        # Create a WebSocket connection to create a session
        with client.websocket_connect("/v1/transcribe/stream") as ws:
            # Receive session_start
            msg = ws.receive_json()
            assert msg["type"] == "session_start"

            # Now check the sessions list
            response = client.get("/v1/sessions")
            data = response.json()

            # Should have at least one session
            assert data["count"] >= 1
            assert len(data["sessions"]) >= 1

            # Session should have expected fields
            session = data["sessions"][0]
            assert "session_id" in session
            assert "state" in session
            assert "created_at" in session


class TestSessionMetricsEndpoint:
    """Test GET /v1/sessions/metrics endpoint."""

    def test_get_metrics_returns_aggregates(self, client):
        """Metrics endpoint should return aggregate metrics."""
        response = client.get("/v1/sessions/metrics")

        assert response.status_code == 200
        data = response.json()

        assert "active_sessions" in data
        assert "total_sessions" in data
        assert "total_audio_bytes" in data
        assert "total_audio_duration_ms" in data


class TestSessionTerminateEndpoint:
    """Test DELETE /v1/sessions/{session_id} endpoint."""

    def test_terminate_nonexistent_returns_404(self, client):
        """Terminating nonexistent session should return 404."""
        response = client.delete("/v1/sessions/nonexistent-session-id")

        assert response.status_code == 404


class TestHealthEndpointWithSessions:
    """Test that health endpoint includes session information."""

    def test_health_includes_active_session_count(self, client):
        """Health endpoint should include active session count."""
        response = client.get("/v1/health")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert data["status"] == "ok"
        assert "active_sessions" in data
        assert isinstance(data["active_sessions"], int)

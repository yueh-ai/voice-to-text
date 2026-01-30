"""
WebSocket streaming endpoint tests.

Contract:
- WS /v1/transcribe/stream accepts audio chunks
- Returns partial results during speech
- Returns final result after silence/stop
"""

import pytest
import base64
import json

from httpx import ASGITransport, AsyncClient


@pytest.fixture
def audio_chunk():
    """A chunk of audio data (base64 encoded for WebSocket)."""
    # 20ms of audio at 16kHz, 16-bit = 640 bytes
    raw_audio = b"\x00\x01" * 320
    return base64.b64encode(raw_audio).decode()


class TestWebSocketStream:
    """WebSocket streaming tests."""

    async def test_stream_connection_succeeds(self, client):
        """Should be able to connect to the stream endpoint."""
        from transcription_service.main import app
        from starlette.testclient import TestClient

        with TestClient(app) as sync_client:
            with sync_client.websocket_connect("/v1/transcribe/stream") as ws:
                # Connection succeeded if we get here
                ws.send_json({"type": "stop"})

    async def test_stream_stop_command_closes_cleanly(self, client):
        """Sending stop command should close the connection."""
        from transcription_service.main import app
        from starlette.testclient import TestClient

        with TestClient(app) as sync_client:
            with sync_client.websocket_connect("/v1/transcribe/stream") as ws:
                ws.send_json({"type": "stop"})
                # Should close without error

    async def test_stream_audio_returns_response(self, client, audio_chunk):
        """Sending audio should return some response."""
        from transcription_service.main import app
        from starlette.testclient import TestClient

        with TestClient(app) as sync_client:
            with sync_client.websocket_connect("/v1/transcribe/stream") as ws:
                # Send audio chunk
                ws.send_json({"type": "audio", "data": audio_chunk})

                # Should receive a response (partial or acknowledgment)
                response = ws.receive_json()
                assert "type" in response

                ws.send_json({"type": "stop"})

    async def test_stream_returns_partial_with_text(self, client, audio_chunk):
        """Audio chunks should produce partial results with text."""
        from transcription_service.main import app
        from starlette.testclient import TestClient

        with TestClient(app) as sync_client:
            with sync_client.websocket_connect("/v1/transcribe/stream") as ws:
                # Send multiple audio chunks to ensure we get a partial
                for _ in range(5):
                    ws.send_json({"type": "audio", "data": audio_chunk})

                # Collect responses
                responses = []
                for _ in range(5):
                    try:
                        response = ws.receive_json()
                        responses.append(response)
                    except Exception:
                        break

                # At least one should be a partial with text
                partials = [r for r in responses if r.get("type") == "partial"]
                assert len(partials) > 0
                assert any("text" in p for p in partials)

                ws.send_json({"type": "stop"})

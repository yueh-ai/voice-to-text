"""
REST transcription endpoint tests.

Contract:
- POST /v1/transcribe accepts audio and returns transcription
- Response contains 'text' and 'duration_ms' fields
"""

import pytest


async def test_transcribe_returns_text_for_audio(client, audio_bytes):
    """Transcribe endpoint should return text for valid audio."""
    response = await client.post(
        "/v1/transcribe",
        content=audio_bytes,
        headers={"Content-Type": "audio/raw"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "text" in data
    assert isinstance(data["text"], str)
    assert len(data["text"]) > 0


async def test_transcribe_returns_duration(client, audio_bytes):
    """Transcribe endpoint should return processing duration."""
    response = await client.post(
        "/v1/transcribe",
        content=audio_bytes,
        headers={"Content-Type": "audio/raw"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "duration_ms" in data
    assert isinstance(data["duration_ms"], (int, float))
    assert data["duration_ms"] >= 0


async def test_transcribe_rejects_empty_audio(client):
    """Transcribe endpoint should reject empty audio."""
    response = await client.post(
        "/v1/transcribe",
        content=b"",
        headers={"Content-Type": "audio/raw"}
    )

    assert response.status_code == 400

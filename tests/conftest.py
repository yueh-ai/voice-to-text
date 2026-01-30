import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Async HTTP client for testing the API."""
    from transcription_service.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def audio_bytes():
    """Sample audio data (raw PCM bytes)."""
    # 16kHz, 16-bit mono = 32000 bytes per second
    # 500ms of audio = 16000 bytes
    duration_ms = 500
    bytes_per_ms = 32
    return b"\x00\x01" * (duration_ms * bytes_per_ms // 2)

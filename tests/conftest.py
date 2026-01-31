import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    """Sync test client that triggers app lifespan."""
    from transcription_service.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def audio_bytes():
    """Sample audio data (raw PCM bytes)."""
    # 16kHz, 16-bit mono = 32000 bytes per second
    # 500ms of audio = 16000 bytes
    duration_ms = 500
    bytes_per_ms = 32
    return b"\x00\x01" * (duration_ms * bytes_per_ms // 2)

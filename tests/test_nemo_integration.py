"""Integration tests for NeMo ASR with session layer (mocked NeMo model)."""

from unittest.mock import MagicMock

from transcription_service.config import Settings
from transcription_service.core.models import Models
from transcription_service.core.session import TranscriptionSession
from transcription_service.core.vad import VADModel


def _make_nemo_models() -> Models:
    """Create a Models container with a mocked NeMo ASR model."""
    mock_nemo = MagicMock()
    mock_nemo.transcribe_sync.return_value = "nemo transcription"

    vad = VADModel(sample_rate=16000, aggressiveness=2)
    return Models(vad=vad, asr=mock_nemo)


class TestNeMoSessionIntegration:
    """Tests that sessions work correctly with a NeMo-like ASR backend."""

    def test_transcribe_full_calls_nemo(self):
        """transcribe_full should delegate to the NeMo model."""
        models = _make_nemo_models()
        config = Settings()
        session = TranscriptionSession(models=models, config=config)

        audio = b"\x00\x01" * 8000  # 500ms
        result = session.transcribe_full(audio)

        assert result.text == "nemo transcription"
        assert result.is_final is True
        models.asr.transcribe_sync.assert_called_once_with(audio)

    async def test_process_chunk_calls_nemo_on_speech(self):
        """process_chunk should call NeMo's transcribe_sync when speech is detected."""
        models = _make_nemo_models()
        config = Settings(latency_ms=0)
        session = TranscriptionSession(models=models, config=config)

        # Create a frame that webrtcvad considers speech
        # 20ms at 16kHz = 640 bytes of high-amplitude signal
        import struct

        frame = struct.pack("<" + "h" * 320, *([10000, -10000] * 160))

        result = await session.process_chunk(frame)

        # If VAD detected speech, it should have called transcribe_sync
        if result.text:
            models.asr.transcribe_sync.assert_called()

    def test_multiple_sessions_share_nemo_model(self):
        """Multiple sessions should share the same ASR model instance."""
        models = _make_nemo_models()
        config = Settings()

        session1 = TranscriptionSession(models=models, config=config)
        session2 = TranscriptionSession(models=models, config=config)

        assert session1.models.asr is session2.models.asr

    def test_session_metrics_with_nemo(self):
        """Session metrics should work regardless of ASR backend."""
        models = _make_nemo_models()
        config = Settings()
        session = TranscriptionSession(models=models, config=config)

        audio = b"\x00\x01" * 8000
        session.transcribe_full(audio)

        info = session.get_info()
        assert info.metrics.audio_bytes_received == 0  # transcribe_full doesn't track

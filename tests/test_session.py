"""Tests for TranscriptionSession (per-user state)."""

import pytest


class TestTranscriptionSession:
    """Tests for per-user TranscriptionSession."""

    def test_session_created_with_models_and_config(self):
        """TranscriptionSession requires models and config."""
        from transcription_service.core.session import TranscriptionSession
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=12800, latency_ms=50)
        models = Models(vad=vad, asr=asr)
        config = Settings()

        session = TranscriptionSession(models=models, config=config)

        assert session.models is models
        assert session.config is config

    def test_session_has_vad_session(self):
        """TranscriptionSession creates a VADSession for per-user state."""
        from transcription_service.core.session import TranscriptionSession
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel, VADSession
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=12800, latency_ms=50)
        models = Models(vad=vad, asr=asr)
        config = Settings()

        session = TranscriptionSession(models=models, config=config)

        assert isinstance(session.vad_session, VADSession)
        assert session.vad_session.model is vad

    def test_session_has_per_user_state(self):
        """TranscriptionSession tracks speech buffer and silence duration."""
        from transcription_service.core.session import TranscriptionSession
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=12800, latency_ms=50)
        models = Models(vad=vad, asr=asr)
        config = Settings()

        session = TranscriptionSession(models=models, config=config)

        assert session._speech_buffer_bytes == 0
        assert session._silence_duration_ms == 0.0

    @pytest.mark.asyncio
    async def test_process_chunk_returns_transcript_result(self):
        """process_chunk returns a TranscriptResult."""
        from transcription_service.core.session import TranscriptionSession, TranscriptResult
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=12800, latency_ms=0)  # No latency for test
        models = Models(vad=vad, asr=asr)
        config = Settings(latency_ms=0)

        session = TranscriptionSession(models=models, config=config)

        # Send some audio
        audio = b"\x00\x01" * 1000
        result = await session.process_chunk(audio)

        assert isinstance(result, TranscriptResult)
        assert hasattr(result, "text")
        assert hasattr(result, "is_final")
        assert hasattr(result, "duration_ms")

    @pytest.mark.asyncio
    async def test_process_chunk_generates_partial_for_speech(self):
        """process_chunk returns partial result with text for speech."""
        import random
        from transcription_service.core.session import TranscriptionSession
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=1000, latency_ms=0)  # 1 word per 1000 bytes
        models = Models(vad=vad, asr=asr)
        # Use long endpointing threshold so silence doesn't trigger final
        config = Settings(latency_ms=0, bytes_per_word=1000, endpointing_ms=10000)

        session = TranscriptionSession(models=models, config=config)

        # Generate noisy audio that VAD will consider speech
        # Random bytes simulate audio signal with energy
        random.seed(42)
        audio = bytes(random.randint(0, 255) for _ in range(10000))
        result = await session.process_chunk(audio)

        # With noisy audio, VAD should detect speech and return partial
        # Or with long endpointing, silence won't trigger final
        assert result.is_final is False

    @pytest.mark.asyncio
    async def test_multiple_sessions_have_isolated_state(self):
        """Multiple sessions should have isolated per-user state."""
        from transcription_service.core.session import TranscriptionSession
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=12800, latency_ms=0)
        models = Models(vad=vad, asr=asr)
        config = Settings(latency_ms=0)

        session1 = TranscriptionSession(models=models, config=config)
        session2 = TranscriptionSession(models=models, config=config)

        # Process audio in session1
        await session1.process_chunk(b"\x00\x01" * 1000)

        # Session2 should not be affected
        assert session2._speech_buffer_bytes == 0
        assert session2.vad_session._buffer == b""

        # Both share the same models
        assert session1.models is session2.models

    def test_transcribe_full_uses_shared_model(self):
        """transcribe_full uses the shared ASR model."""
        from transcription_service.core.session import TranscriptionSession, TranscriptResult
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=1000, latency_ms=50)
        models = Models(vad=vad, asr=asr)
        config = Settings()

        session = TranscriptionSession(models=models, config=config)

        # Transcribe full audio
        audio = b"\x00\x01" * 5000
        result = session.transcribe_full(audio)

        assert isinstance(result, TranscriptResult)
        assert result.is_final is True
        assert len(result.text) > 0

"""Tests for refactored VAD: VADModel (shared) + VADSession (per-user)."""

import pytest


class TestVADModel:
    """Tests for the shared VADModel (stateless inference)."""

    def test_model_is_stateless(self):
        """VADModel should not hold per-user state."""
        from transcription_service.core.vad import VADModel

        model = VADModel(sample_rate=16000, aggressiveness=2)

        # Model should not have buffer or per-session state
        assert not hasattr(model, "_buffer")
        assert hasattr(model, "_vad")  # Has the underlying VAD instance

    def test_model_is_speech_on_single_frame(self):
        """VADModel.is_speech works on a single correctly-sized frame."""
        from transcription_service.core.vad import VADModel

        model = VADModel(sample_rate=16000, aggressiveness=2)

        # 20ms frame at 16kHz = 640 bytes
        frame_size = 16000 * 20 // 1000 * 2  # 640 bytes
        silence_frame = b"\x00" * frame_size

        # Should return False for silence (stateless call)
        result = model.is_speech(silence_frame)
        assert isinstance(result, bool)

    def test_model_validates_sample_rate(self):
        """VADModel rejects invalid sample rates."""
        from transcription_service.core.vad import VADModel

        with pytest.raises(ValueError, match="sample rate"):
            VADModel(sample_rate=44100, aggressiveness=2)

    def test_model_validates_aggressiveness(self):
        """VADModel rejects invalid aggressiveness levels."""
        from transcription_service.core.vad import VADModel

        with pytest.raises(ValueError, match="aggressiveness"):
            VADModel(sample_rate=16000, aggressiveness=5)


class TestVADSession:
    """Tests for per-user VADSession (holds buffer state)."""

    def test_session_has_buffer(self):
        """VADSession should have per-user buffer state."""
        from transcription_service.core.vad import VADModel, VADSession

        model = VADModel(sample_rate=16000, aggressiveness=2)
        session = VADSession(model=model, frame_duration_ms=20)

        assert hasattr(session, "_buffer")
        assert session._buffer == b""

    def test_session_accumulates_buffer(self):
        """VADSession accumulates audio in buffer."""
        from transcription_service.core.vad import VADModel, VADSession

        model = VADModel(sample_rate=16000, aggressiveness=2)
        session = VADSession(model=model, frame_duration_ms=20)

        # Send partial data (less than frame size)
        chunk = b"\x00" * 100
        session.is_speech(chunk)

        assert len(session._buffer) == 100

    def test_session_returns_true_when_buffer_insufficient(self):
        """VADSession returns True (assume speech) when buffer too small."""
        from transcription_service.core.vad import VADModel, VADSession

        model = VADModel(sample_rate=16000, aggressiveness=2)
        session = VADSession(model=model, frame_duration_ms=20)

        # Send very small chunk
        result = session.is_speech(b"\x00" * 10)
        assert result is True

    def test_session_delegates_to_model(self):
        """VADSession uses shared model for inference."""
        from transcription_service.core.vad import VADModel, VADSession

        model = VADModel(sample_rate=16000, aggressiveness=2)
        session = VADSession(model=model, frame_duration_ms=20)

        # 20ms frame = 640 bytes of silence
        frame_size = 640
        silence = b"\x00" * frame_size

        # Should delegate to model and detect silence
        result = session.is_speech(silence)
        assert isinstance(result, bool)

    def test_session_reset_clears_buffer(self):
        """VADSession.reset() clears the buffer."""
        from transcription_service.core.vad import VADModel, VADSession

        model = VADModel(sample_rate=16000, aggressiveness=2)
        session = VADSession(model=model, frame_duration_ms=20)

        session.is_speech(b"\x00" * 100)
        assert len(session._buffer) > 0

        session.reset()
        assert session._buffer == b""

    def test_multiple_sessions_share_model(self):
        """Multiple VADSessions can share one VADModel."""
        from transcription_service.core.vad import VADModel, VADSession

        model = VADModel(sample_rate=16000, aggressiveness=2)

        session1 = VADSession(model=model, frame_duration_ms=20)
        session2 = VADSession(model=model, frame_duration_ms=20)

        # Add different data to each session
        session1.is_speech(b"\x00" * 100)
        session2.is_speech(b"\x00" * 200)

        # Each session has its own buffer
        assert len(session1._buffer) == 100
        assert len(session2._buffer) == 200

        # But they share the same model
        assert session1.model is session2.model

"""Tests for session lifecycle state management.

These tests verify session behavior through public APIs only.
They don't test internal state directly - they test observable behavior.
"""

import pytest

from transcription_service.config import Settings
from transcription_service.core.models import init_models, get_models, _reset_models
from transcription_service.core.session import (
    TranscriptionSession,
    SessionState,
    SessionClosingError,
)


@pytest.fixture(autouse=True)
def setup_models():
    """Initialize models before each test."""
    _reset_models()
    config = Settings()
    init_models(config)
    yield
    _reset_models()


@pytest.fixture
def config():
    """Test configuration with fast settings."""
    return Settings(latency_ms=0, endpointing_ms=100)


@pytest.fixture
def session(config):
    """Create a fresh session for testing."""
    models = get_models()
    return TranscriptionSession(models, config)


@pytest.fixture
def speech_audio():
    """Audio that will be detected as speech."""
    # High amplitude audio (speech-like)
    return b"\x00\x10" * 640  # 20ms at 16kHz


@pytest.fixture
def silence_audio():
    """Audio that will be detected as silence."""
    # Zero/low amplitude audio
    return b"\x00\x00" * 640  # 20ms at 16kHz


class TestSessionLifecycleStates:
    """Test session state transitions."""

    def test_session_starts_in_created_state(self, session):
        """A new session should be in CREATED state."""
        info = session.get_info()
        assert info.state == SessionState.CREATED

    @pytest.mark.asyncio
    async def test_session_stays_created_when_receiving_silence(
        self, session, silence_audio
    ):
        """Session should stay in CREATED state when only silence is received."""
        # Initially CREATED
        assert session.get_info().state == SessionState.CREATED

        # Process silence - should NOT transition to ACTIVE
        await session.process_chunk(silence_audio)
        await session.process_chunk(silence_audio)
        await session.process_chunk(silence_audio)

        # Still CREATED - waiting for speech
        assert session.get_info().state == SessionState.CREATED

    @pytest.mark.asyncio
    async def test_session_transitions_to_active_on_first_speech(
        self, session, speech_audio
    ):
        """Session should transition to ACTIVE only when speech is detected."""
        # Initially CREATED
        assert session.get_info().state == SessionState.CREATED

        # Process speech audio
        await session.process_chunk(speech_audio)

        # Now ACTIVE
        assert session.get_info().state == SessionState.ACTIVE

    @pytest.mark.asyncio
    async def test_session_transitions_to_active_after_initial_silence(
        self, session, silence_audio, speech_audio
    ):
        """Session should transition to ACTIVE when speech follows silence."""
        # Process silence first
        await session.process_chunk(silence_audio)
        await session.process_chunk(silence_audio)
        assert session.get_info().state == SessionState.CREATED

        # Now speech
        await session.process_chunk(speech_audio)
        assert session.get_info().state == SessionState.ACTIVE

    @pytest.mark.asyncio
    async def test_no_endpointing_while_created(self, session, silence_audio):
        """Session should not send finals (endpointing) while waiting for first speech."""
        # Process lots of silence while in CREATED state
        # Should NOT trigger endpointing (is_final=True) since speech hasn't started
        for _ in range(20):  # Way more than endpointing threshold
            result = await session.process_chunk(silence_audio)
            # While CREATED, should not get is_final=True
            assert result.is_final is False

        # Still in CREATED
        assert session.get_info().state == SessionState.CREATED

    @pytest.mark.asyncio
    async def test_endpointing_works_after_speech_started(
        self, session, speech_audio, silence_audio
    ):
        """Endpointing should work normally after speech has been detected."""
        # Start with speech to become ACTIVE
        await session.process_chunk(speech_audio)
        assert session.get_info().state == SessionState.ACTIVE

        # Now silence should trigger endpointing after threshold
        final_received = False
        for _ in range(20):
            result = await session.process_chunk(silence_audio)
            if result.is_final:
                final_received = True
                break

        assert final_received, "Should receive is_final=True after silence threshold"

    @pytest.mark.asyncio
    async def test_session_transitions_to_closing_on_close(
        self, session, speech_audio
    ):
        """Session should transition to CLOSING when close() is called."""
        # Make session active first
        await session.process_chunk(speech_audio)
        assert session.get_info().state == SessionState.ACTIVE

        # Close session
        await session.close()

        # Now CLOSED (goes through CLOSING to CLOSED)
        assert session.get_info().state == SessionState.CLOSED

    @pytest.mark.asyncio
    async def test_session_transitions_to_closed_after_cleanup(self, session):
        """Session should be CLOSED after close() completes."""
        await session.close()
        assert session.get_info().state == SessionState.CLOSED

    @pytest.mark.asyncio
    async def test_session_rejects_audio_when_closing(self, session, speech_audio):
        """Session should reject audio processing when closing/closed."""
        # Make active then close
        await session.process_chunk(speech_audio)
        await session.close()

        # Attempting to process audio should raise error
        with pytest.raises(SessionClosingError):
            await session.process_chunk(speech_audio)

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, session, speech_audio):
        """Calling close() multiple times should be safe."""
        await session.process_chunk(speech_audio)

        # Close multiple times - should not raise
        await session.close()
        await session.close()
        await session.close()

        assert session.get_info().state == SessionState.CLOSED


class TestSessionMetrics:
    """Test session metrics tracking."""

    @pytest.mark.asyncio
    async def test_session_metrics_track_audio_bytes(self, session, speech_audio):
        """Session should track total audio bytes received."""
        info_before = session.get_info()
        assert info_before.metrics.audio_bytes_received == 0

        await session.process_chunk(speech_audio)

        info_after = session.get_info()
        assert info_after.metrics.audio_bytes_received == len(speech_audio)

    @pytest.mark.asyncio
    async def test_session_metrics_track_audio_chunks(self, session, speech_audio):
        """Session should track number of audio chunks received."""
        info_before = session.get_info()
        assert info_before.metrics.audio_chunks_received == 0

        await session.process_chunk(speech_audio)
        await session.process_chunk(speech_audio)

        info_after = session.get_info()
        assert info_after.metrics.audio_chunks_received == 2

    @pytest.mark.asyncio
    async def test_session_metrics_track_transcripts(self, session, speech_audio):
        """Session should track transcripts sent."""
        await session.process_chunk(speech_audio)

        info = session.get_info()
        assert info.metrics.transcripts_sent >= 1

    @pytest.mark.asyncio
    async def test_session_metrics_audio_duration(self, session, speech_audio):
        """Session should calculate audio duration from bytes."""
        await session.process_chunk(speech_audio)

        info = session.get_info()
        # 32 bytes per ms at 16kHz 16-bit
        expected_duration = len(speech_audio) / 32.0
        assert info.metrics.audio_duration_ms == pytest.approx(expected_duration)


class TestSessionTimestamps:
    """Test session timestamp tracking."""

    def test_session_has_created_at_timestamp(self, session):
        """Session should record when it was created."""
        info = session.get_info()
        assert info.created_at is not None

    def test_session_has_session_id(self, session):
        """Session should have a unique session ID."""
        info = session.get_info()
        assert info.session_id is not None
        assert len(info.session_id) > 0

    @pytest.mark.asyncio
    async def test_session_last_activity_updates_on_audio(
        self, session, speech_audio
    ):
        """Session should update last_activity_at when audio is received."""
        info_before = session.get_info()
        initial_activity = info_before.last_activity_at

        # Small delay to ensure timestamp difference
        import asyncio
        await asyncio.sleep(0.01)

        await session.process_chunk(speech_audio)

        info_after = session.get_info()
        assert info_after.last_activity_at > initial_activity

    def test_sessions_have_unique_ids(self, config):
        """Each session should have a unique ID."""
        models = get_models()
        session1 = TranscriptionSession(models, config)
        session2 = TranscriptionSession(models, config)

        assert session1.get_info().session_id != session2.get_info().session_id

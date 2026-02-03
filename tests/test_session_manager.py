"""Tests for the session manager.

These tests verify session manager behavior through public APIs only.
They test observable behavior like session creation, retrieval, limits, and cleanup.
"""

import asyncio
import pytest

from transcription_service.config import Settings
from transcription_service.core.models import init_models, get_models, _reset_models
from transcription_service.core.session_manager import (
    SessionManager,
    SessionManagerConfig,
    SessionLimitExceeded,
    SessionNotFound,
)
from transcription_service.core.session import SessionState


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
    """Test configuration."""
    return Settings(latency_ms=0)


@pytest.fixture
def manager_config():
    """Session manager configuration for testing."""
    return SessionManagerConfig(
        max_sessions=5,
        idle_timeout_seconds=1.0,
        cleanup_interval_seconds=0.5,
    )


@pytest.fixture
async def session_manager(config, manager_config):
    """Create and start a session manager for testing."""
    models = get_models()
    manager = SessionManager(models, config, manager_config)
    await manager.start()
    yield manager
    await manager.stop()


class TestSessionCreation:
    """Test session creation behavior."""

    @pytest.mark.asyncio
    async def test_create_session_returns_new_session(self, session_manager):
        """create_session should return a new session."""
        session = await session_manager.create_session()
        assert session is not None
        assert session.get_info().session_id is not None

    @pytest.mark.asyncio
    async def test_create_session_assigns_unique_ids(self, session_manager):
        """Each created session should have a unique ID."""
        session1 = await session_manager.create_session()
        session2 = await session_manager.create_session()
        session3 = await session_manager.create_session()

        ids = {
            session1.get_info().session_id,
            session2.get_info().session_id,
            session3.get_info().session_id,
        }
        assert len(ids) == 3  # All unique

    @pytest.mark.asyncio
    async def test_create_session_respects_max_limit(self, session_manager, manager_config):
        """Should be able to create up to max_sessions."""
        sessions = []
        for _ in range(manager_config.max_sessions):
            session = await session_manager.create_session()
            sessions.append(session)

        assert len(sessions) == manager_config.max_sessions

    @pytest.mark.asyncio
    async def test_create_session_raises_on_limit_exceeded(
        self, session_manager, manager_config
    ):
        """Should raise SessionLimitExceeded when max sessions reached."""
        # Fill up to the limit
        for _ in range(manager_config.max_sessions):
            await session_manager.create_session()

        # Next one should fail
        with pytest.raises(SessionLimitExceeded):
            await session_manager.create_session()


class TestSessionRetrieval:
    """Test session retrieval behavior."""

    @pytest.mark.asyncio
    async def test_get_session_returns_existing_session(self, session_manager):
        """get_session should return the session with matching ID."""
        created = await session_manager.create_session()
        session_id = created.get_info().session_id

        retrieved = await session_manager.get_session(session_id)
        assert retrieved.get_info().session_id == session_id

    @pytest.mark.asyncio
    async def test_get_session_raises_on_not_found(self, session_manager):
        """get_session should raise SessionNotFound for unknown ID."""
        with pytest.raises(SessionNotFound):
            await session_manager.get_session("nonexistent-id")


class TestSessionClosure:
    """Test session closure behavior."""

    @pytest.mark.asyncio
    async def test_close_session_removes_from_registry(self, session_manager):
        """close_session should remove the session from the manager."""
        session = await session_manager.create_session()
        session_id = session.get_info().session_id

        await session_manager.close_session(session_id)

        # Should no longer be retrievable
        with pytest.raises(SessionNotFound):
            await session_manager.get_session(session_id)

    @pytest.mark.asyncio
    async def test_close_session_calls_session_close(self, session_manager):
        """close_session should close the underlying session."""
        session = await session_manager.create_session()
        session_id = session.get_info().session_id

        # Process some audio to make it active
        speech_audio = b"\x00\x10" * 640
        await session.process_chunk(speech_audio)

        await session_manager.close_session(session_id)

        # Session should be in CLOSED state
        assert session.get_info().state == SessionState.CLOSED

    @pytest.mark.asyncio
    async def test_close_nonexistent_session_is_silent(self, session_manager):
        """close_session should not raise for nonexistent session."""
        # Should not raise
        await session_manager.close_session("nonexistent-id")


class TestIdleCleanup:
    """Test idle session cleanup behavior."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_idle_sessions(self, config):
        """Sessions idle beyond timeout should be cleaned up."""
        models = get_models()
        # Very short timeout for testing
        manager_config = SessionManagerConfig(
            max_sessions=10,
            initial_speech_timeout_seconds=0.1,  # 100ms for CREATED
            idle_timeout_seconds=0.1,  # 100ms for ACTIVE
            cleanup_interval_seconds=0.05,  # 50ms
        )
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        try:
            session = await manager.create_session()
            session_id = session.get_info().session_id

            # Wait for cleanup to run
            await asyncio.sleep(0.3)

            # Session should have been cleaned up
            with pytest.raises(SessionNotFound):
                await manager.get_session(session_id)
        finally:
            await manager.stop()

    @pytest.mark.asyncio
    async def test_created_sessions_have_shorter_timeout(self, config):
        """Sessions in CREATED state should timeout faster than ACTIVE sessions."""
        models = get_models()
        # Short initial timeout, longer idle timeout
        manager_config = SessionManagerConfig(
            max_sessions=10,
            initial_speech_timeout_seconds=0.1,  # 100ms for CREATED
            idle_timeout_seconds=2.0,  # 2s for ACTIVE (much longer)
            cleanup_interval_seconds=0.05,  # 50ms
        )
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        try:
            session = await manager.create_session()
            session_id = session.get_info().session_id

            # Session is in CREATED state (no speech yet)
            from transcription_service.core.session import SessionState
            assert session.get_info().state == SessionState.CREATED

            # Wait for initial speech timeout (but less than idle timeout)
            await asyncio.sleep(0.25)

            # CREATED session should have been cleaned up due to shorter timeout
            with pytest.raises(SessionNotFound):
                await manager.get_session(session_id)
        finally:
            await manager.stop()

    @pytest.mark.asyncio
    async def test_active_sessions_use_longer_timeout(self, config):
        """Sessions in ACTIVE state should use the longer idle timeout."""
        models = get_models()
        manager_config = SessionManagerConfig(
            max_sessions=10,
            initial_speech_timeout_seconds=0.1,  # 100ms for CREATED
            idle_timeout_seconds=0.5,  # 500ms for ACTIVE
            cleanup_interval_seconds=0.05,  # 50ms
        )
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        try:
            session = await manager.create_session()
            session_id = session.get_info().session_id

            # Make session ACTIVE by sending speech
            speech_audio = b"\x00\x10" * 640
            await session.process_chunk(speech_audio)

            from transcription_service.core.session import SessionState
            assert session.get_info().state == SessionState.ACTIVE

            # Wait longer than initial_speech_timeout but less than idle_timeout
            await asyncio.sleep(0.25)

            # Session should still exist (using longer ACTIVE timeout)
            retrieved = await manager.get_session(session_id)
            assert retrieved is not None

            # Wait for full idle timeout
            await asyncio.sleep(0.4)

            # Now it should be cleaned up
            with pytest.raises(SessionNotFound):
                await manager.get_session(session_id)
        finally:
            await manager.stop()

    @pytest.mark.asyncio
    async def test_cleanup_removes_closed_sessions(self, config):
        """Already closed sessions should be removed from registry."""
        models = get_models()
        manager_config = SessionManagerConfig(
            max_sessions=10,
            idle_timeout_seconds=60.0,  # Long timeout
            cleanup_interval_seconds=0.05,  # Fast cleanup
        )
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        try:
            session = await manager.create_session()
            session_id = session.get_info().session_id

            # Close the session directly (simulating error/disconnect)
            await session.close()

            # Wait for cleanup
            await asyncio.sleep(0.15)

            # Should be removed from registry
            with pytest.raises(SessionNotFound):
                await manager.get_session(session_id)
        finally:
            await manager.stop()


class TestSessionCounting:
    """Test session counting and metrics."""

    @pytest.mark.asyncio
    async def test_get_active_count_excludes_closing(self, session_manager):
        """Active count should not include closing/closed sessions."""
        session1 = await session_manager.create_session()
        session2 = await session_manager.create_session()
        session3 = await session_manager.create_session()

        assert session_manager.get_active_count() == 3

        # Close one session
        await session_manager.close_session(session1.get_info().session_id)

        assert session_manager.get_active_count() == 2

    @pytest.mark.asyncio
    async def test_get_all_sessions_returns_session_info(self, session_manager):
        """get_all_sessions should return info for all sessions."""
        session1 = await session_manager.create_session()
        session2 = await session_manager.create_session()

        all_sessions = session_manager.get_all_sessions()

        assert len(all_sessions) == 2
        session_ids = {s.session_id for s in all_sessions}
        assert session1.get_info().session_id in session_ids
        assert session2.get_info().session_id in session_ids

    @pytest.mark.asyncio
    async def test_get_aggregate_metrics_sums_correctly(self, session_manager):
        """Aggregate metrics should sum across all sessions."""
        session1 = await session_manager.create_session()
        session2 = await session_manager.create_session()

        # Process audio on both sessions
        speech_audio = b"\x00\x10" * 640
        await session1.process_chunk(speech_audio)
        await session2.process_chunk(speech_audio)
        await session2.process_chunk(speech_audio)

        metrics = session_manager.get_aggregate_metrics()

        assert metrics["active_sessions"] == 2
        assert metrics["total_audio_bytes"] == len(speech_audio) * 3
        assert metrics["total_chunks"] == 3


class TestGracefulShutdown:
    """Test graceful shutdown behavior."""

    @pytest.mark.asyncio
    async def test_stop_closes_all_sessions(self, config):
        """stop() should close all active sessions."""
        models = get_models()
        manager_config = SessionManagerConfig(max_sessions=10)
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        session1 = await manager.create_session()
        session2 = await manager.create_session()

        # Stop the manager
        await manager.stop()

        # All sessions should be closed
        assert session1.get_info().state == SessionState.CLOSED
        assert session2.get_info().state == SessionState.CLOSED

    @pytest.mark.asyncio
    async def test_stop_can_be_called_multiple_times(self, config):
        """stop() should be idempotent."""
        models = get_models()
        manager_config = SessionManagerConfig(max_sessions=10)
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        await manager.create_session()

        # Should not raise on multiple stops
        await manager.stop()
        await manager.stop()


class TestConcurrentAccess:
    """Test concurrent session operations."""

    @pytest.mark.asyncio
    async def test_concurrent_session_creation(self, session_manager):
        """Multiple concurrent create_session calls should be safe."""
        # Create sessions concurrently
        tasks = [session_manager.create_session() for _ in range(5)]
        sessions = await asyncio.gather(*tasks)

        # All should succeed and have unique IDs
        session_ids = {s.get_info().session_id for s in sessions}
        assert len(session_ids) == 5

    @pytest.mark.asyncio
    async def test_concurrent_limit_enforcement(self, config):
        """Limit should be enforced correctly under concurrent access."""
        models = get_models()
        manager_config = SessionManagerConfig(max_sessions=3)
        manager = SessionManager(models, config, manager_config)
        await manager.start()

        try:
            # Try to create 10 sessions concurrently with limit of 3
            tasks = [manager.create_session() for _ in range(10)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count successes and failures
            successes = [r for r in results if not isinstance(r, Exception)]
            failures = [r for r in results if isinstance(r, SessionLimitExceeded)]

            assert len(successes) == 3
            assert len(failures) == 7
        finally:
            await manager.stop()

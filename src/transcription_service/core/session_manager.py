"""Centralized session registry with lifecycle management.

Provides SessionManager for tracking, creating, and cleaning up
transcription sessions.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from transcription_service.config import Settings
from transcription_service.core.models import Models
from transcription_service.core.session import (
    SessionInfo,
    SessionState,
    TranscriptionSession,
)

logger = logging.getLogger(__name__)


@dataclass
class SessionManagerConfig:
    """Configuration for session manager."""

    max_sessions: int = 1000
    idle_timeout_seconds: float = 300.0  # 5 minutes
    cleanup_interval_seconds: float = 30.0


class SessionLimitExceeded(Exception):
    """Raised when max concurrent sessions reached."""

    pass


class SessionNotFound(Exception):
    """Raised when session ID not found."""

    pass


class SessionManager:
    """
    Centralized session registry with lifecycle management.

    Responsibilities:
    - Create and track sessions
    - Enforce concurrent session limits
    - Clean up idle/disconnected sessions
    - Provide session inspection
    """

    def __init__(
        self,
        models: Models,
        config: Settings,
        manager_config: Optional[SessionManagerConfig] = None,
    ):
        """
        Initialize session manager.

        Args:
            models: Shared Models container
            config: Application settings
            manager_config: Session manager specific configuration
        """
        self.models = models
        self.config = config
        self.manager_config = manager_config or SessionManagerConfig()

        self._sessions: Dict[str, TranscriptionSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Session manager started")

    async def stop(self):
        """Stop cleanup task and close all sessions."""
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # Close all active sessions
        async with self._lock:
            for session in list(self._sessions.values()):
                await session.close()
            self._sessions.clear()

        logger.info("Session manager stopped")

    async def create_session(self) -> TranscriptionSession:
        """
        Create a new session.

        Returns:
            Newly created TranscriptionSession

        Raises:
            SessionLimitExceeded: If max sessions reached
        """
        async with self._lock:
            # Count active (non-closing) sessions
            active_count = sum(
                1
                for s in self._sessions.values()
                if s.get_info().state not in (SessionState.CLOSING, SessionState.CLOSED)
            )

            if active_count >= self.manager_config.max_sessions:
                raise SessionLimitExceeded(
                    f"Maximum {self.manager_config.max_sessions} concurrent sessions reached"
                )

            session = TranscriptionSession(self.models, self.config)
            session_id = session.get_info().session_id
            self._sessions[session_id] = session

            logger.debug(f"Created session {session_id}, active: {active_count + 1}")
            return session

    async def get_session(self, session_id: str) -> TranscriptionSession:
        """
        Get session by ID.

        Args:
            session_id: The session ID to look up

        Returns:
            The TranscriptionSession with the given ID

        Raises:
            SessionNotFound: If session ID not found
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise SessionNotFound(f"Session {session_id} not found")
            return session

    async def close_session(self, session_id: str) -> bool:
        """
        Close and remove a session.

        Args:
            session_id: The session ID to close

        Returns:
            True if session was found and closed, False if not found
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                await session.close()
                del self._sessions[session_id]
                logger.debug(f"Closed session {session_id}")
                return True
            return False

    async def _cleanup_loop(self):
        """Background task to clean up idle sessions."""
        while True:
            await asyncio.sleep(self.manager_config.cleanup_interval_seconds)
            await self._cleanup_idle_sessions()

    async def _cleanup_idle_sessions(self):
        """Remove sessions that have been idle too long."""
        now = datetime.now(timezone.utc)
        timeout = timedelta(seconds=self.manager_config.idle_timeout_seconds)

        to_close: List[str] = []

        async with self._lock:
            for session_id, session in self._sessions.items():
                info = session.get_info()
                if info.state == SessionState.CLOSED:
                    to_close.append(session_id)
                elif now - info.last_activity_at > timeout:
                    logger.info(f"Session {session_id} idle timeout")
                    to_close.append(session_id)

        # Close sessions outside the main lock to avoid deadlock
        for session_id in to_close:
            await self.close_session(session_id)

        if to_close:
            logger.info(f"Cleaned up {len(to_close)} idle sessions")

    # Inspection methods

    def get_active_count(self) -> int:
        """Get count of active sessions (non-blocking snapshot)."""
        return sum(
            1
            for s in self._sessions.values()
            if s.get_info().state in (SessionState.CREATED, SessionState.ACTIVE)
        )

    def get_all_sessions(self) -> List[SessionInfo]:
        """Get info for all sessions."""
        return [s.get_info() for s in self._sessions.values()]

    def get_aggregate_metrics(self) -> dict:
        """Get aggregated metrics across all sessions."""
        total_audio_bytes = 0
        total_chunks = 0
        total_transcripts = 0

        for session in self._sessions.values():
            metrics = session.get_info().metrics
            total_audio_bytes += metrics.audio_bytes_received
            total_chunks += metrics.audio_chunks_received
            total_transcripts += metrics.transcripts_sent

        return {
            "active_sessions": self.get_active_count(),
            "total_sessions": len(self._sessions),
            "total_audio_bytes": total_audio_bytes,
            "total_audio_duration_ms": total_audio_bytes / 32.0,
            "total_chunks": total_chunks,
            "total_transcripts": total_transcripts,
        }

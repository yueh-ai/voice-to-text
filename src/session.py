from enum import Enum
from typing import Optional
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)


class SessionState(Enum):
    INIT = "init"
    STREAMING = "streaming"
    FINALIZING = "finalizing"
    CLOSED = "closed"


class TranscriptionSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = SessionState.INIT
        self.created_at = datetime.utcnow()
        self.audio_buffer = bytearray()
        self.transcript_parts = []
        self._lock = asyncio.Lock()

    async def start_streaming(self):
        async with self._lock:
            if self.state != SessionState.INIT:
                raise ValueError(f"Cannot start streaming from state {self.state}")
            self.state = SessionState.STREAMING
            logger.info(f"Session {self.session_id}: INIT -> STREAMING")

    async def add_audio_chunk(self, audio_data: bytes):
        async with self._lock:
            if self.state != SessionState.STREAMING:
                raise ValueError(f"Cannot add audio in state {self.state}")
            self.audio_buffer.extend(audio_data)

    async def finalize(self):
        async with self._lock:
            if self.state == SessionState.CLOSED:
                return
            if self.state == SessionState.STREAMING:
                self.state = SessionState.FINALIZING
                logger.info(f"Session {self.session_id}: STREAMING -> FINALIZING")

    async def close(self):
        async with self._lock:
            previous_state = self.state
            self.state = SessionState.CLOSED
            self.audio_buffer.clear()
            logger.info(f"Session {self.session_id}: {previous_state} -> CLOSED")

    def get_state(self) -> SessionState:
        return self.state


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, TranscriptionSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, session_id: str) -> TranscriptionSession:
        async with self._lock:
            if session_id in self.sessions:
                logger.warning(f"Session {session_id} already exists, closing old session")
                await self.sessions[session_id].close()

            session = TranscriptionSession(session_id)
            self.sessions[session_id] = session
            logger.info(f"Created session {session_id}")
            return session

    async def get_session(self, session_id: str) -> Optional[TranscriptionSession]:
        async with self._lock:
            return self.sessions.get(session_id)

    async def close_session(self, session_id: str):
        async with self._lock:
            if session_id in self.sessions:
                await self.sessions[session_id].close()
                del self.sessions[session_id]
                logger.info(f"Removed session {session_id}")

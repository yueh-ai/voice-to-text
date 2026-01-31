"""Per-user transcription session.

Provides TranscriptionSession which manages all per-user state
while using shared models for inference.
"""

import asyncio
import uuid
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from transcription_service.config import Settings
from transcription_service.core.models import Models
from transcription_service.core.vad import VADSession


class SessionState(Enum):
    """Lifecycle states for a transcription session."""

    CREATED = "created"  # Session exists, not yet active
    ACTIVE = "active"  # Processing audio
    CLOSING = "closing"  # Cleanup in progress
    CLOSED = "closed"  # Terminal state


class SessionClosingError(Exception):
    """Raised when attempting to process audio on a closing/closed session."""

    pass


@dataclass
class SessionMetrics:
    """Per-session metrics."""

    audio_bytes_received: int = 0
    audio_chunks_received: int = 0
    transcripts_sent: int = 0
    partials_sent: int = 0
    finals_sent: int = 0
    errors_sent: int = 0

    @property
    def audio_duration_ms(self) -> float:
        """Estimated audio duration based on bytes (16kHz, 16-bit)."""
        return self.audio_bytes_received / 32.0  # 32 bytes per ms


@dataclass
class SessionInfo:
    """Session metadata for inspection."""

    session_id: str
    state: SessionState
    created_at: datetime
    last_activity_at: datetime
    metrics: SessionMetrics


@dataclass
class TranscriptResult:
    """Result from ASR processing."""

    text: str
    is_final: bool
    duration_ms: float


class TranscriptionSession:
    """
    Per-user transcription session state.

    Manages audio buffering, VAD state, and silence tracking for a single user.
    Uses shared models from the Models container for inference.
    """

    def __init__(self, models: Models, config: Settings):
        """
        Initialize transcription session.

        Args:
            models: Shared Models container
            config: Application settings
        """
        self.models = models
        self.config = config

        # Session identity and lifecycle
        self._session_id: str = str(uuid.uuid4())
        self._state: SessionState = SessionState.CREATED
        self._created_at: datetime = datetime.now(timezone.utc)
        self._last_activity_at: datetime = self._created_at
        self._metrics: SessionMetrics = SessionMetrics()

        # Per-user VAD session
        self.vad_session = VADSession(
            model=models.vad,
            frame_duration_ms=config.vad_frame_ms,
        )

        # Per-user state
        self._speech_buffer_bytes = 0
        self._silence_duration_ms = 0.0

    async def process_chunk(self, audio: bytes) -> TranscriptResult:
        """
        Process an audio chunk.

        Args:
            audio: Raw PCM audio bytes (16-bit)

        Returns:
            TranscriptResult with transcription status:
            - text with is_final=False if speech detected
            - empty text with is_final=False if silence (waiting for more)
            - empty text with is_final=True if silence threshold reached

        Raises:
            SessionClosingError: If session is closing or closed
        """
        # Check if session is accepting audio
        if self._state in (SessionState.CLOSING, SessionState.CLOSED):
            raise SessionClosingError("Session is closing, cannot accept audio")

        # Transition from CREATED to ACTIVE on first audio
        if self._state == SessionState.CREATED:
            self._state = SessionState.ACTIVE

        # Update activity timestamp and metrics
        self._last_activity_at = datetime.now(timezone.utc)
        self._metrics.audio_bytes_received += len(audio)
        self._metrics.audio_chunks_received += 1

        # Simulate processing latency
        if self.config.latency_ms > 0:
            await asyncio.sleep(self.config.latency_ms / 1000)

        # Calculate chunk duration
        chunk_duration_ms = self._chunk_duration_ms(audio)

        if self.vad_session.is_speech(audio):
            self._speech_buffer_bytes += len(audio)
            self._silence_duration_ms = 0

            # Generate text using shared ASR model
            text = self.models.asr.transcribe_sync(audio)

            self._metrics.transcripts_sent += 1
            self._metrics.partials_sent += 1

            return TranscriptResult(
                text=text,
                is_final=False,
                duration_ms=self.config.latency_ms,
            )
        else:
            self._silence_duration_ms += chunk_duration_ms

            if self._silence_duration_ms >= self.config.endpointing_ms:
                self._reset()
                self._metrics.transcripts_sent += 1
                self._metrics.finals_sent += 1
                return TranscriptResult(
                    text="",
                    is_final=True,
                    duration_ms=self.config.latency_ms,
                )

            # Silence detected but threshold not reached - return empty partial
            self._metrics.transcripts_sent += 1
            return TranscriptResult(
                text="",
                is_final=False,
                duration_ms=self.config.latency_ms,
            )

    def transcribe_full(self, audio: bytes) -> TranscriptResult:
        """
        Transcribe a complete audio file (non-streaming).

        Args:
            audio: Complete raw PCM audio bytes

        Returns:
            TranscriptResult with full transcription
        """
        text = self.models.asr.transcribe_sync(audio)
        return TranscriptResult(
            text=text,
            is_final=True,
            duration_ms=self.config.latency_ms,
        )

    async def close(self):
        """Initiate graceful shutdown of the session."""
        if self._state in (SessionState.CLOSING, SessionState.CLOSED):
            return

        self._state = SessionState.CLOSING

        # Cleanup resources
        self.vad_session.reset()
        self._reset()

        self._state = SessionState.CLOSED

    def get_info(self) -> SessionInfo:
        """Return session metadata for inspection."""
        return SessionInfo(
            session_id=self._session_id,
            state=self._state,
            created_at=self._created_at,
            last_activity_at=self._last_activity_at,
            metrics=copy(self._metrics),  # Return copy to prevent external mutation
        )

    def _chunk_duration_ms(self, audio: bytes) -> float:
        """Calculate the duration of an audio chunk in milliseconds."""
        bytes_per_ms = self.config.bytes_per_second / 1000
        return len(audio) / bytes_per_ms

    def _reset(self):
        """Reset internal state."""
        self._speech_buffer_bytes = 0
        self._silence_duration_ms = 0.0
        self.vad_session.reset()

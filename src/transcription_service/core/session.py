"""Per-user transcription session.

Provides TranscriptionSession which manages all per-user state
while using shared models for inference.
"""

import asyncio
from dataclasses import dataclass

from transcription_service.config import Settings
from transcription_service.core.models import Models
from transcription_service.core.vad import VADSession


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
        """
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
            return TranscriptResult(
                text=text,
                is_final=False,
                duration_ms=self.config.latency_ms,
            )
        else:
            self._silence_duration_ms += chunk_duration_ms

            if self._silence_duration_ms >= self.config.endpointing_ms:
                self._reset()
                return TranscriptResult(
                    text="",
                    is_final=True,
                    duration_ms=self.config.latency_ms,
                )
            # Silence detected but threshold not reached - return empty partial
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

    def _chunk_duration_ms(self, audio: bytes) -> float:
        """Calculate the duration of an audio chunk in milliseconds."""
        bytes_per_ms = self.config.bytes_per_second / 1000
        return len(audio) / bytes_per_ms

    def _reset(self):
        """Reset internal state."""
        self._speech_buffer_bytes = 0
        self._silence_duration_ms = 0.0
        self.vad_session.reset()

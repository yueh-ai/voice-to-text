"""Mock ASR model combining VAD and text generation."""

import asyncio
from dataclasses import dataclass

from transcription_service.config import Settings
from transcription_service.core.vad import VADProcessor
from transcription_service.core.text_generator import TextGenerator


@dataclass
class TranscriptResult:
    """Result from ASR processing."""

    text: str
    is_final: bool
    duration_ms: float


class MockASRModel:
    """
    Mock ASR that uses real VAD and fake text generation.

    Behavior:
    - Receives audio chunks
    - Uses VAD to detect speech vs silence
    - Generates fake text proportional to speech audio length
    - Emits partial results during speech
    - Emits final result after silence threshold
    """

    def __init__(self, config: Settings):
        """
        Initialize mock ASR model.

        Args:
            config: Application settings
        """
        self.vad = VADProcessor(
            sample_rate=config.sample_rate,
            aggressiveness=config.vad_aggressiveness,
            frame_duration_ms=config.vad_frame_ms,
        )
        self.text_gen = TextGenerator(config.bytes_per_word)
        self.config = config

        # State
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
        await asyncio.sleep(self.config.latency_ms / 1000)

        # Calculate chunk duration
        chunk_duration_ms = self._chunk_duration_ms(audio)

        if self.vad.is_speech(audio):
            self._speech_buffer_bytes += len(audio)
            self._silence_duration_ms = 0

            # Generate text proportional to this chunk
            text = self.text_gen.generate(len(audio))
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
        # Generate text based on total audio length
        text = self.text_gen.generate(len(audio))
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
        self.vad.reset()

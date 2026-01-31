"""Mock ASR model for testing and development.

Provides a stateless mock ASR model that generates fake text
proportional to audio byte length.
"""

import asyncio

from transcription_service.core.text_generator import TextGenerator


class MockASRModel:
    """
    Shared mock ASR model (stateless).

    Generates fake transcription text proportional to audio length.
    This class does not hold per-user state and can be shared across sessions.
    """

    def __init__(self, bytes_per_word: int = 12800, latency_ms: int = 50):
        """
        Initialize mock ASR model.

        Args:
            bytes_per_word: Audio bytes per generated word
            latency_ms: Simulated processing latency in milliseconds
        """
        self.text_gen = TextGenerator(bytes_per_word)
        self.latency_ms = latency_ms

    async def transcribe(self, audio: bytes) -> str:
        """
        Transcribe audio to text (async, with simulated latency).

        Args:
            audio: Raw PCM audio bytes

        Returns:
            Generated fake transcription text
        """
        await asyncio.sleep(self.latency_ms / 1000)
        return self.text_gen.generate(len(audio))

    def transcribe_sync(self, audio: bytes) -> str:
        """
        Transcribe audio to text (sync, no latency simulation).

        Args:
            audio: Raw PCM audio bytes

        Returns:
            Generated fake transcription text
        """
        return self.text_gen.generate(len(audio))

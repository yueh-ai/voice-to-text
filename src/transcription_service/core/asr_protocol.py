"""ASR model protocol for type-safe polymorphism.

Defines the interface that all ASR backends (MockASRModel, NeMoASRModel)
must implement so Models.asr is polymorphic.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ASRModel(Protocol):
    """Protocol for ASR model implementations."""

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe audio to text (async)."""
        ...

    def transcribe_sync(self, audio: bytes) -> str:
        """Transcribe audio to text (sync)."""
        ...

"""Shared model container for dependency injection.

This module provides a singleton container for shared model instances
that are loaded once at app startup and shared across all user sessions.
"""

from dataclasses import dataclass

from transcription_service.config import Settings
from transcription_service.core.vad import VADModel


# Forward declaration to avoid circular import
# The actual import happens inside functions
_models: "Models | None" = None


@dataclass
class Models:
    """Container for shared model instances (singleton)."""

    vad: VADModel
    asr: "MockASRModel"  # Forward reference


def init_models(config: Settings) -> "Models":
    """
    Load all models. Called once at app startup.

    Args:
        config: Application settings

    Returns:
        Models container with initialized models
    """
    global _models

    # Import here to avoid circular dependency
    from transcription_service.core.mock_asr import MockASRModel

    _models = Models(
        vad=VADModel(
            sample_rate=config.sample_rate,
            aggressiveness=config.vad_aggressiveness,
        ),
        asr=MockASRModel(
            bytes_per_word=config.bytes_per_word,
            latency_ms=config.latency_ms,
        ),
    )
    return _models


def get_models() -> "Models":
    """
    Get shared models (FastAPI dependency).

    Returns:
        Models container

    Raises:
        RuntimeError: If models not initialized
    """
    if _models is None:
        raise RuntimeError("Models not initialized. Call init_models() first.")
    return _models


def _reset_models() -> None:
    """Reset models to uninitialized state. For testing only."""
    global _models
    _models = None

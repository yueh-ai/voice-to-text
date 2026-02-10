"""Shared model container for dependency injection.

This module provides a singleton container for shared model instances
that are loaded once at app startup and shared across all user sessions.
"""

import logging
from dataclasses import dataclass
from typing import Any

from transcription_service.config import Settings
from transcription_service.core.vad import VADModel

logger = logging.getLogger(__name__)

# Forward declaration to avoid circular import
# The actual import happens inside functions
_models: "Models | None" = None


@dataclass
class Models:
    """Container for shared model instances (singleton)."""

    vad: VADModel
    asr: Any  # ASRModel protocol (MockASRModel or NeMoASRModel)


def init_models(config: Settings) -> "Models":
    """
    Load all models. Called once at app startup.

    Selects ASR backend based on config.asr_engine:
    - "mock": MockASRModel (default, no GPU required)
    - "nemo": NeMoASRModel (requires nemo-toolkit and torch)

    Args:
        config: Application settings

    Returns:
        Models container with initialized models
    """
    global _models

    vad = VADModel(
        sample_rate=config.sample_rate,
        aggressiveness=config.vad_aggressiveness,
    )

    if config.asr_engine == "nemo":
        from transcription_service.core.nemo_asr import NeMoASRModel

        logger.info("Initializing NeMo ASR engine...")
        asr = NeMoASRModel(
            model_name=config.nemo_model_name,
            device=config.nemo_device,
            warmup=config.nemo_warmup,
            sample_rate=config.sample_rate,
            rtf_warning_threshold=config.nemo_rtf_warning_threshold,
        )
        asr.load()
    else:
        from transcription_service.core.mock_asr import MockASRModel

        asr = MockASRModel(
            bytes_per_word=config.bytes_per_word,
            latency_ms=config.latency_ms,
        )

    _models = Models(vad=vad, asr=asr)
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

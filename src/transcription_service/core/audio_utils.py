"""Stateless audio utility functions for ASR preprocessing.

Converts raw PCM audio bytes to formats suitable for NeMo inference.
Only imported by NeMoASRModel, so numpy is only needed when NeMo is active.
"""

import numpy as np


def pcm_bytes_to_float32(audio_bytes: bytes) -> np.ndarray:
    """Convert raw PCM 16-bit little-endian bytes to float32 array in [-1.0, 1.0].

    Args:
        audio_bytes: Raw PCM audio (16-bit signed LE).

    Returns:
        Float32 numpy array normalized to [-1.0, 1.0].
    """
    if len(audio_bytes) == 0:
        return np.array([], dtype=np.float32)

    samples = np.frombuffer(audio_bytes, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def audio_duration_seconds(audio: np.ndarray, sample_rate: int) -> float:
    """Calculate the duration of an audio array in seconds.

    Args:
        audio: Audio samples as numpy array.
        sample_rate: Sample rate in Hz.

    Returns:
        Duration in seconds.
    """
    if len(audio) == 0:
        return 0.0
    return len(audio) / sample_rate

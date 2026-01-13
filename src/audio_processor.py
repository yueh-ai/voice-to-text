import numpy as np
from collections import deque
from typing import List, Optional
import logging

from src.config import AudioConfig

logger = logging.getLogger(__name__)


class AudioProcessor:
    """
    Handles audio buffering, chunking, and context window management
    for streaming ASR inference.
    """

    def __init__(self, config: AudioConfig):
        self.config = config
        self.sample_rate = config.sample_rate

        # Calculate sizes in samples
        self.chunk_size_samples = int(config.sample_rate * config.chunk_duration)
        self.left_context_samples = int(config.sample_rate * config.left_context_duration)
        self.right_context_samples = int(config.sample_rate * config.right_context_duration)

        # Calculate sizes in bytes (16-bit PCM = 2 bytes per sample)
        self.chunk_size_bytes = self.chunk_size_samples * 2
        self.left_context_bytes = self.left_context_samples * 2

        # Current audio buffer (incoming bytes)
        self.buffer = bytearray()

        # Left context buffer (ring buffer of previous chunks)
        max_context_chunks = int(
            config.left_context_duration / config.chunk_duration
        ) + 1
        self.left_context_buffer: deque = deque(maxlen=max_context_chunks)

        # Statistics
        self.total_bytes_processed = 0
        self.chunks_processed = 0

        logger.info(
            f"AudioProcessor initialized: "
            f"chunk={config.chunk_duration}s, "
            f"left_context={config.left_context_duration}s, "
            f"right_context={config.right_context_duration}s"
        )

    def add_audio(self, audio_bytes: bytes) -> None:
        """
        Add audio data to the buffer.

        Args:
            audio_bytes: Raw PCM audio bytes (16-bit little-endian)
        """
        self.buffer.extend(audio_bytes)
        self.total_bytes_processed += len(audio_bytes)

    def get_inference_chunks(self) -> List[np.ndarray]:
        """
        Extract all ready chunks from the buffer with context.

        Returns:
            List of numpy arrays, each containing:
            [left_context + chunk + right_context]

        Note:
            Right context is not yet implemented (requires lookahead).
            Currently returns [left_context + chunk].
        """
        chunks = []

        while len(self.buffer) >= self.chunk_size_bytes:
            # Extract one chunk worth of bytes
            chunk_bytes = bytes(self.buffer[:self.chunk_size_bytes])
            del self.buffer[:self.chunk_size_bytes]

            # Convert to numpy array
            chunk_audio = self._bytes_to_audio(chunk_bytes)

            # Build inference input with left context
            inference_input = self._build_with_context(chunk_audio)

            chunks.append(inference_input)

            # Update left context buffer
            self.left_context_buffer.append(chunk_audio)

            self.chunks_processed += 1

        return chunks

    def _build_with_context(self, chunk: np.ndarray) -> np.ndarray:
        """
        Combine left context + chunk for inference.

        Args:
            chunk: Current audio chunk

        Returns:
            Concatenated audio with context
        """
        if not self.left_context_buffer:
            # No context yet, return just the chunk
            return chunk

        # Concatenate all left context
        left_context_list = list(self.left_context_buffer)
        left_context = np.concatenate(left_context_list)

        # Limit to max left context duration
        if len(left_context) > self.left_context_samples:
            left_context = left_context[-self.left_context_samples:]

        # Combine context + chunk
        return np.concatenate([left_context, chunk])

    def _bytes_to_audio(self, audio_bytes: bytes) -> np.ndarray:
        """
        Convert PCM bytes to numpy float32 array.

        Args:
            audio_bytes: Raw PCM audio bytes (16-bit little-endian)

        Returns:
            Numpy array of float32 values in range [-1.0, 1.0]
        """
        # Convert bytes to int16 array
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)

        # Convert to float32 in range [-1, 1]
        audio_float = audio_int16.astype(np.float32) / 32768.0

        return audio_float

    def flush(self) -> Optional[np.ndarray]:
        """
        Flush any remaining audio in the buffer.

        Returns:
            Remaining audio with context, or None if buffer empty
        """
        if len(self.buffer) == 0:
            return None

        # Convert remaining bytes
        remaining_bytes = bytes(self.buffer)
        self.buffer.clear()

        remaining_audio = self._bytes_to_audio(remaining_bytes)

        # Add context
        result = self._build_with_context(remaining_audio)

        logger.debug(f"Flushed {len(remaining_bytes)} bytes")

        return result

    def reset(self) -> None:
        """Reset all buffers and state."""
        self.buffer.clear()
        self.left_context_buffer.clear()
        self.total_bytes_processed = 0
        self.chunks_processed = 0
        logger.debug("AudioProcessor reset")

    def get_buffer_duration(self) -> float:
        """
        Get the duration of audio currently in the buffer.

        Returns:
            Duration in seconds
        """
        num_samples = len(self.buffer) // 2  # 2 bytes per sample
        return num_samples / self.sample_rate

    def get_stats(self) -> dict:
        """
        Get processing statistics.

        Returns:
            Dictionary with stats
        """
        return {
            "total_bytes_processed": self.total_bytes_processed,
            "chunks_processed": self.chunks_processed,
            "buffer_size_bytes": len(self.buffer),
            "buffer_duration_secs": self.get_buffer_duration(),
            "left_context_chunks": len(self.left_context_buffer)
        }

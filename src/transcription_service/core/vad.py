"""WebRTC VAD wrapper for voice activity detection.

Provides two classes:
- VADModel: Shared model weights/instance (stateless inference)
- VADSession: Per-user session state (buffer management)
"""

import webrtcvad


class VADModel:
    """
    Shared VAD model instance (stateless).

    Holds the WebRTC VAD instance and performs inference on single frames.
    This class does not hold any per-user state and can be shared across sessions.
    """

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        """
        Initialize VAD model.

        Args:
            sample_rate: Audio sample rate in Hz (8000, 16000, 32000, or 48000)
            aggressiveness: VAD aggressiveness level (0-3, higher = more aggressive)
        """
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"Invalid sample rate: {sample_rate}")
        if aggressiveness not in range(4):
            raise ValueError(f"Invalid aggressiveness: {aggressiveness}")

        self.sample_rate = sample_rate
        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: bytes) -> bool:
        """
        Check if a single frame contains speech.

        Args:
            frame: Raw PCM audio frame (must be correct size for frame duration)

        Returns:
            True if speech detected, False otherwise
        """
        try:
            return self._vad.is_speech(frame, self.sample_rate)
        except Exception:
            # If VAD fails, assume speech to avoid dropping audio
            return True


class VADSession:
    """
    Per-user VAD session state.

    Manages audio buffering and delegates inference to the shared VADModel.
    Each user connection should have its own VADSession instance.
    """

    # Valid frame durations in milliseconds
    VALID_FRAME_DURATIONS = (10, 20, 30)

    def __init__(self, model: VADModel, frame_duration_ms: int = 20):
        """
        Initialize VAD session.

        Args:
            model: Shared VADModel instance
            frame_duration_ms: Frame duration in ms (10, 20, or 30)
        """
        if frame_duration_ms not in self.VALID_FRAME_DURATIONS:
            raise ValueError(f"Invalid frame duration: {frame_duration_ms}")

        self.model = model
        self.frame_duration_ms = frame_duration_ms

        # Calculate frame size in bytes (16-bit = 2 bytes per sample)
        samples_per_frame = model.sample_rate * frame_duration_ms // 1000
        self.frame_size_bytes = samples_per_frame * 2

        # Per-user buffer
        self._buffer = b""

    def is_speech(self, audio_chunk: bytes) -> bool:
        """
        Check if audio chunk contains speech.

        Args:
            audio_chunk: Raw PCM audio bytes (16-bit)

        Returns:
            True if speech detected, False otherwise
        """
        # Add to buffer
        self._buffer += audio_chunk

        # If we don't have enough data for a frame, assume speech
        if len(self._buffer) < self.frame_size_bytes:
            return True

        # Process the most recent complete frame
        frame = self._buffer[-self.frame_size_bytes:]
        return self.model.is_speech(frame)

    def process_frames(self, audio_chunk: bytes) -> list[bool]:
        """
        Process audio and return speech detection for each frame.

        Args:
            audio_chunk: Raw PCM audio bytes (16-bit)

        Returns:
            List of booleans, one per complete frame processed
        """
        self._buffer += audio_chunk
        results = []

        while len(self._buffer) >= self.frame_size_bytes:
            frame = self._buffer[: self.frame_size_bytes]
            self._buffer = self._buffer[self.frame_size_bytes:]
            results.append(self.model.is_speech(frame))

        return results

    def reset(self):
        """Clear the internal buffer."""
        self._buffer = b""

    def get_frame_duration_ms(self) -> int:
        """Return the frame duration in milliseconds."""
        return self.frame_duration_ms

    def get_frame_size_bytes(self) -> int:
        """Return the frame size in bytes."""
        return self.frame_size_bytes

"""WebRTC VAD wrapper for voice activity detection."""

import webrtcvad


class VADProcessor:
    """
    Processes audio chunks and detects speech/silence.

    WebRTC VAD requirements:
    - Audio must be 16-bit PCM
    - Sample rate: 8000, 16000, 32000, or 48000 Hz
    - Frame duration: 10, 20, or 30 ms
    """

    # Valid frame durations in milliseconds
    VALID_FRAME_DURATIONS = (10, 20, 30)

    def __init__(
        self,
        sample_rate: int = 16000,
        aggressiveness: int = 2,
        frame_duration_ms: int = 20,
    ):
        """
        Initialize VAD processor.

        Args:
            sample_rate: Audio sample rate in Hz (8000, 16000, 32000, or 48000)
            aggressiveness: VAD aggressiveness level (0-3, higher = more aggressive)
            frame_duration_ms: Frame duration in ms (10, 20, or 30)
        """
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"Invalid sample rate: {sample_rate}")
        if aggressiveness not in range(4):
            raise ValueError(f"Invalid aggressiveness: {aggressiveness}")
        if frame_duration_ms not in self.VALID_FRAME_DURATIONS:
            raise ValueError(f"Invalid frame duration: {frame_duration_ms}")

        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self._vad = webrtcvad.Vad(aggressiveness)

        # Calculate frame size in bytes (16-bit = 2 bytes per sample)
        samples_per_frame = sample_rate * frame_duration_ms // 1000
        self.frame_size_bytes = samples_per_frame * 2

        # Buffer for accumulating audio
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

        try:
            return self._vad.is_speech(frame, self.sample_rate)
        except Exception:
            # If VAD fails, assume speech to avoid dropping audio
            return True

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

            try:
                results.append(self._vad.is_speech(frame, self.sample_rate))
            except Exception:
                results.append(True)

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

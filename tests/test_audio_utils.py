"""Tests for audio utility functions."""

import struct

import numpy as np
import pytest

from transcription_service.core.audio_utils import audio_duration_seconds, pcm_bytes_to_float32


class TestPCMBytesToFloat32:
    """Tests for PCM to float32 conversion."""

    def test_silence_converts_to_zeros(self):
        """All-zero PCM bytes should produce all-zero float32 array."""
        silence = b"\x00\x00" * 100
        result = pcm_bytes_to_float32(silence)
        assert result.dtype == np.float32
        np.testing.assert_array_equal(result, np.zeros(100, dtype=np.float32))

    def test_max_positive_value(self):
        """Max positive int16 (32767) should map close to 1.0."""
        # 32767 as int16 little-endian
        audio = struct.pack("<h", 32767)
        result = pcm_bytes_to_float32(audio)
        assert len(result) == 1
        assert result[0] == pytest.approx(32767 / 32768.0, abs=1e-6)

    def test_max_negative_value(self):
        """Min int16 (-32768) should map to exactly -1.0."""
        audio = struct.pack("<h", -32768)
        result = pcm_bytes_to_float32(audio)
        assert len(result) == 1
        assert result[0] == pytest.approx(-1.0, abs=1e-6)

    def test_output_shape_matches_sample_count(self):
        """Output array length should be half the byte count (16-bit samples)."""
        num_samples = 160
        audio = b"\x00\x01" * num_samples
        result = pcm_bytes_to_float32(audio)
        assert len(result) == num_samples

    def test_empty_input_returns_empty_array(self):
        """Empty bytes should produce an empty float32 array."""
        result = pcm_bytes_to_float32(b"")
        assert result.dtype == np.float32
        assert len(result) == 0

    def test_output_range(self):
        """All output values should be in [-1.0, 1.0]."""
        # Random-ish PCM data
        audio = bytes(range(256)) * 2
        result = pcm_bytes_to_float32(audio)
        assert np.all(result >= -1.0)
        assert np.all(result <= 1.0)


class TestAudioDurationSeconds:
    """Tests for audio duration calculation."""

    def test_one_second_at_16khz(self):
        """16000 samples at 16kHz should be 1.0 second."""
        audio = np.zeros(16000, dtype=np.float32)
        assert audio_duration_seconds(audio, 16000) == pytest.approx(1.0)

    def test_half_second_at_16khz(self):
        """8000 samples at 16kHz should be 0.5 seconds."""
        audio = np.zeros(8000, dtype=np.float32)
        assert audio_duration_seconds(audio, 16000) == pytest.approx(0.5)

    def test_empty_audio(self):
        """Empty audio should have zero duration."""
        audio = np.array([], dtype=np.float32)
        assert audio_duration_seconds(audio, 16000) == 0.0

    def test_different_sample_rate(self):
        """Duration should scale with sample rate."""
        audio = np.zeros(48000, dtype=np.float32)
        assert audio_duration_seconds(audio, 48000) == pytest.approx(1.0)

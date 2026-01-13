import pytest
import numpy as np
from src.audio_processor import AudioProcessor
from src.config import AudioConfig


@pytest.fixture
def audio_config():
    """Default audio configuration for testing"""
    return AudioConfig(
        sample_rate=16000,
        chunk_duration=1.0,
        left_context_duration=10.0,
        right_context_duration=2.0
    )


@pytest.fixture
def processor(audio_config):
    """AudioProcessor instance with default config"""
    return AudioProcessor(audio_config)


def test_processor_initialization(audio_config):
    """Test AudioProcessor initializes correctly"""
    processor = AudioProcessor(audio_config)

    assert processor.sample_rate == 16000
    assert processor.chunk_size_samples == 16000  # 1 second at 16kHz
    assert processor.left_context_samples == 160000  # 10 seconds
    assert len(processor.buffer) == 0
    assert processor.chunks_processed == 0


def test_add_audio(processor):
    """Test adding audio to buffer"""
    # Create 1 second of audio (16000 samples = 32000 bytes)
    audio = np.random.rand(16000).astype(np.float32)
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()

    processor.add_audio(audio_bytes)

    assert len(processor.buffer) == 32000
    assert processor.total_bytes_processed == 32000


def test_chunking_single_chunk(processor):
    """Test extracting a single chunk"""
    # Create exactly 1 second of audio
    audio = np.random.rand(16000).astype(np.float32)
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()

    processor.add_audio(audio_bytes)
    chunks = processor.get_inference_chunks()

    assert len(chunks) == 1
    assert chunks[0].shape[0] == 16000  # 1 second, no context yet


def test_chunking_multiple_chunks(processor):
    """Test extracting multiple chunks"""
    # Create 2.5 seconds of audio
    audio = np.random.rand(40000).astype(np.float32)  # 2.5 seconds
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()

    processor.add_audio(audio_bytes)
    chunks = processor.get_inference_chunks()

    # Should get 2 complete chunks (0.5 seconds remains in buffer)
    assert len(chunks) == 2
    assert processor.chunks_processed == 2

    # Buffer should contain remaining 0.5 seconds
    assert len(processor.buffer) == 16000  # 0.5 seconds = 8000 samples = 16000 bytes


def test_context_window(processor):
    """Test that left context is accumulated"""
    # First chunk (no context)
    audio1 = np.random.rand(16000).astype(np.float32)
    audio1_bytes = (audio1 * 32768).astype(np.int16).tobytes()
    processor.add_audio(audio1_bytes)
    chunks1 = processor.get_inference_chunks()

    assert len(chunks1) == 1
    assert chunks1[0].shape[0] == 16000  # No context yet

    # Second chunk (should have 1 second of left context)
    audio2 = np.random.rand(16000).astype(np.float32)
    audio2_bytes = (audio2 * 32768).astype(np.int16).tobytes()
    processor.add_audio(audio2_bytes)
    chunks2 = processor.get_inference_chunks()

    assert len(chunks2) == 1
    assert chunks2[0].shape[0] == 32000  # 1s context + 1s chunk


def test_bytes_to_audio_conversion(processor):
    """Test PCM bytes to numpy array conversion"""
    # Create known audio values
    audio_int16 = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16)
    audio_bytes = audio_int16.tobytes()

    audio_float = processor._bytes_to_audio(audio_bytes)

    assert audio_float.dtype == np.float32
    assert len(audio_float) == 5

    # Check conversion is correct
    assert audio_float[0] == pytest.approx(0.0, abs=1e-5)
    assert audio_float[1] == pytest.approx(0.5, abs=1e-3)
    assert audio_float[2] == pytest.approx(-0.5, abs=1e-3)
    assert audio_float[3] == pytest.approx(1.0, abs=1e-3)
    assert audio_float[4] == pytest.approx(-1.0, abs=1e-3)


def test_flush_remaining_audio(processor):
    """Test flushing remaining audio from buffer"""
    # Add less than 1 chunk
    audio = np.random.rand(8000).astype(np.float32)  # 0.5 seconds
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()

    processor.add_audio(audio_bytes)

    # Should have no complete chunks
    chunks = processor.get_inference_chunks()
    assert len(chunks) == 0

    # Flush should return the remaining audio
    flushed = processor.flush()
    assert flushed is not None
    assert len(flushed) == 8000  # 0.5 seconds

    # Buffer should be empty after flush
    assert len(processor.buffer) == 0


def test_reset(processor):
    """Test resetting processor state"""
    # Add some audio
    audio = np.random.rand(32000).astype(np.float32)
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()
    processor.add_audio(audio_bytes)
    processor.get_inference_chunks()

    # Reset
    processor.reset()

    assert len(processor.buffer) == 0
    assert len(processor.left_context_buffer) == 0
    assert processor.total_bytes_processed == 0
    assert processor.chunks_processed == 0


def test_get_buffer_duration(processor):
    """Test buffer duration calculation"""
    # Add 2 seconds of audio
    audio = np.random.rand(32000).astype(np.float32)
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()
    processor.add_audio(audio_bytes)

    duration = processor.get_buffer_duration()
    assert duration == pytest.approx(2.0, abs=0.01)


def test_get_stats(processor):
    """Test statistics tracking"""
    # Add 3 seconds of audio
    audio = np.random.rand(48000).astype(np.float32)
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()
    processor.add_audio(audio_bytes)

    # Process chunks
    chunks = processor.get_inference_chunks()

    stats = processor.get_stats()

    assert stats['total_bytes_processed'] == 96000  # 48000 samples * 2 bytes
    assert stats['chunks_processed'] == 3
    assert stats['left_context_chunks'] == 3
    assert stats['buffer_duration_secs'] == 0.0  # All processed


def test_long_context_accumulation(processor):
    """Test that left context doesn't exceed maximum duration"""
    # Add 15 seconds of audio (more than 10s max context)
    for i in range(15):
        audio = np.random.rand(16000).astype(np.float32)
        audio_bytes = (audio * 32768).astype(np.int16).tobytes()
        processor.add_audio(audio_bytes)
        chunks = processor.get_inference_chunks()

    # Last chunk should have max 10 seconds of context + 1 second chunk
    audio = np.random.rand(16000).astype(np.float32)
    audio_bytes = (audio * 32768).astype(np.int16).tobytes()
    processor.add_audio(audio_bytes)
    chunks = processor.get_inference_chunks()

    # Should be <= 11 seconds total (10s context + 1s chunk)
    assert chunks[0].shape[0] <= 176000  # 11 seconds at 16kHz


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

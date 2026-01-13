import pytest
import numpy as np
import time
from src.endpointing import Endpointing
from src.config import EndpointingConfig


@pytest.fixture
def energy_config():
    """Energy-based endpointing configuration"""
    return EndpointingConfig(
        strategy="energy",
        energy_threshold=0.01,
        silence_duration=0.5,  # Short duration for faster tests
        vad_enabled=False
    )


@pytest.fixture
def endpointing(energy_config):
    """Endpointing instance with energy-based detection"""
    return Endpointing(energy_config)


def test_initialization(energy_config):
    """Test endpointing initializes correctly"""
    ep = Endpointing(energy_config)

    assert ep.strategy == "energy"
    assert ep.silence_start is None
    assert ep.speech_detected is False


def test_speech_detection(endpointing):
    """Test that speech is detected correctly"""
    # Create loud audio (speech)
    audio = np.random.rand(16000).astype(np.float32) * 0.5  # RMS ~0.14

    endpoint = endpointing.process_audio(audio)

    assert endpoint is False  # No endpoint yet
    assert endpointing.speech_detected is True
    assert endpointing.silence_start is None


def test_silence_detection(endpointing):
    """Test that silence is detected"""
    # Create very quiet audio (silence)
    audio = np.random.rand(16000).astype(np.float32) * 0.001  # RMS ~0.0003

    endpoint = endpointing.process_audio(audio)

    assert endpoint is False  # Not long enough yet
    assert endpointing.is_in_silence() is True


def test_endpoint_trigger(endpointing):
    """Test that endpoint is triggered after sufficient silence"""
    # Create silence
    silence_audio = np.random.rand(8000).astype(np.float32) * 0.001

    # First chunk - start silence tracking
    endpoint1 = endpointing.process_audio(silence_audio)
    assert endpoint1 is False

    # Wait for silence duration threshold
    time.sleep(0.6)  # config has 0.5s threshold

    # Second chunk - should trigger endpoint
    endpoint2 = endpointing.process_audio(silence_audio)
    assert endpoint2 is True

    # After endpoint, state should be reset
    assert endpointing.is_in_silence() is False


def test_silence_interrupted_by_speech(endpointing):
    """Test that speech resets silence tracking"""
    # Start with silence
    silence_audio = np.random.rand(8000).astype(np.float32) * 0.001
    endpointing.process_audio(silence_audio)

    assert endpointing.is_in_silence() is True

    # Interrupt with speech
    speech_audio = np.random.rand(8000).astype(np.float32) * 0.5
    endpoint = endpointing.process_audio(speech_audio)

    assert endpoint is False
    assert endpointing.is_in_silence() is False
    assert endpointing.speech_detected is True


def test_rms_calculation():
    """Test RMS energy calculation"""
    # Known RMS values
    audio1 = np.zeros(1000, dtype=np.float32)
    rms1 = np.sqrt(np.mean(audio1 ** 2))
    assert rms1 == 0.0

    audio2 = np.ones(1000, dtype=np.float32)
    rms2 = np.sqrt(np.mean(audio2 ** 2))
    assert rms2 == 1.0

    audio3 = np.full(1000, 0.5, dtype=np.float32)
    rms3 = np.sqrt(np.mean(audio3 ** 2))
    assert rms3 == pytest.approx(0.5, abs=0.01)


def test_reset(endpointing):
    """Test resetting endpointing state"""
    # Trigger some silence
    silence_audio = np.random.rand(8000).astype(np.float32) * 0.001
    endpointing.process_audio(silence_audio)

    assert endpointing.is_in_silence() is True

    # Reset
    endpointing.reset()

    assert endpointing.silence_start is None
    assert endpointing.speech_detected is False
    assert endpointing.is_in_silence() is False


def test_get_silence_duration(endpointing):
    """Test silence duration tracking"""
    # Initially no silence
    assert endpointing.get_silence_duration() == 0.0

    # Start silence
    silence_audio = np.random.rand(8000).astype(np.float32) * 0.001
    endpointing.process_audio(silence_audio)

    # Wait a bit
    time.sleep(0.2)

    # Check duration
    duration = endpointing.get_silence_duration()
    assert duration >= 0.2
    assert duration < 0.5  # Should be less than threshold


def test_get_stats(endpointing):
    """Test statistics retrieval"""
    stats = endpointing.get_stats()

    assert 'strategy' in stats
    assert 'in_silence' in stats
    assert 'silence_duration' in stats
    assert 'speech_detected' in stats
    assert 'vad_loaded' in stats

    assert stats['strategy'] == 'energy'
    assert stats['vad_loaded'] is False


def test_multiple_endpoints(endpointing):
    """Test multiple endpoint detections in sequence"""
    silence_audio = np.random.rand(8000).astype(np.float32) * 0.001
    speech_audio = np.random.rand(8000).astype(np.float32) * 0.5

    # First endpoint
    endpointing.process_audio(silence_audio)
    time.sleep(0.6)
    endpoint1 = endpointing.process_audio(silence_audio)
    assert endpoint1 is True

    # Speech again
    endpointing.process_audio(speech_audio)

    # Second endpoint
    endpointing.process_audio(silence_audio)
    time.sleep(0.6)
    endpoint2 = endpointing.process_audio(silence_audio)
    assert endpoint2 is True


def test_energy_threshold_boundary(energy_config):
    """Test audio right at the threshold"""
    ep = Endpointing(energy_config)

    # Create audio with RMS exactly at threshold
    threshold = energy_config.energy_threshold
    # For RMS=threshold, we need audio with std=threshold
    audio_at_threshold = np.random.randn(16000).astype(np.float32) * threshold

    # This is at the boundary, behavior depends on exact values
    # Just verify it doesn't crash
    endpoint = ep.process_audio(audio_at_threshold)
    assert isinstance(endpoint, bool)


def test_very_short_silence(energy_config):
    """Test that very short silence doesn't trigger endpoint"""
    config = EndpointingConfig(
        strategy="energy",
        energy_threshold=0.01,
        silence_duration=2.0,  # Long duration
        vad_enabled=False
    )
    ep = Endpointing(config)

    silence_audio = np.random.rand(8000).astype(np.float32) * 0.001

    # Process silence for less than threshold
    ep.process_audio(silence_audio)
    time.sleep(0.1)
    endpoint = ep.process_audio(silence_audio)

    assert endpoint is False
    assert ep.is_in_silence() is True


# VAD tests - only run when VAD can be loaded
@pytest.mark.skipif(True, reason="Requires NeMo and VAD model")
def test_vad_initialization():
    """Test VAD-based endpointing initialization"""
    config = EndpointingConfig(
        strategy="vad",
        vad_threshold=0.5,
        silence_duration=0.5,
        vad_enabled=True
    )

    ep = Endpointing(config)

    # Check if VAD loaded (may fall back to energy if unavailable)
    if ep.vad_model is not None:
        assert ep.strategy == "vad"
    else:
        assert ep.strategy == "energy"  # Fallback


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

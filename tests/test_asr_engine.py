import pytest
import asyncio
import numpy as np
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.asr_engine import ASREngine, PerformanceMetrics
from src.config import Config, ModelConfig


@pytest.fixture
def config():
    """Test configuration"""
    return Config.load()


@pytest.fixture
async def engine():
    """Fresh ASREngine instance for each test"""
    # Reset singleton
    ASREngine._instance = None
    engine = await ASREngine.get_instance()
    return engine


@pytest.mark.asyncio
async def test_singleton_pattern():
    """Test that ASREngine is a singleton"""
    ASREngine._instance = None

    engine1 = await ASREngine.get_instance()
    engine2 = await ASREngine.get_instance()

    assert engine1 is engine2


def test_performance_metrics():
    """Test performance metrics tracking"""
    metrics = PerformanceMetrics()

    # Record some inferences
    metrics.record_inference(audio_duration=1.0, inference_time=0.5)
    metrics.record_inference(audio_duration=1.0, inference_time=0.3)
    metrics.record_inference(audio_duration=1.0, inference_time=0.4)

    assert metrics.inference_count == 3
    assert metrics.average_rtf == pytest.approx(0.4, abs=0.05)
    assert metrics.overall_rtf == pytest.approx(0.4, abs=0.05)

    stats = metrics.get_stats()
    assert stats['inference_count'] == 3
    assert 'average_rtf' in stats


@pytest.mark.asyncio
async def test_device_detection_cpu(engine, config):
    """Test device detection defaults to CPU without torch"""
    with patch('src.asr_engine.torch', None):
        device = engine._detect_device(config)
        assert device == "cpu"


@pytest.mark.asyncio
async def test_device_detection_cuda_available(engine, config):
    """Test device detection with CUDA available"""
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.get_device_name.return_value = "Tesla T4"
    mock_torch.cuda.get_device_properties.return_value = Mock(total_memory=16e9)

    with patch.dict('sys.modules', {'torch': mock_torch}):
        device = engine._detect_device(config)
        assert device == "cuda"


@pytest.mark.asyncio
async def test_device_detection_cuda_not_available(engine):
    """Test device detection falls back to CPU when CUDA not available"""
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False

    # Config with device="auto"
    config = Config()
    config.model.device = "auto"

    with patch.dict('sys.modules', {'torch': mock_torch}):
        device = engine._detect_device(config)
        assert device == "cpu"


@pytest.mark.asyncio
async def test_device_detection_cuda_required_but_unavailable(engine):
    """Test that requesting CUDA without availability raises error"""
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = False

    # Config with device="cuda" (required)
    config = Config()
    config.model.device = "cuda"

    with patch.dict('sys.modules', {'torch': mock_torch}):
        with pytest.raises(RuntimeError, match="CUDA device requested but not available"):
            engine._detect_device(config)


@pytest.mark.asyncio
async def test_load_model_nemo_not_installed(engine, config):
    """Test graceful error when NeMo not installed"""
    with patch('src.asr_engine.nemo_asr', side_effect=ImportError("No module named 'nemo'")):
        with pytest.raises(RuntimeError, match="NeMo toolkit not installed"):
            await engine.load_model(config)


@pytest.mark.asyncio
async def test_transcribe_without_loaded_model(engine):
    """Test that transcribe fails gracefully when model not loaded"""
    audio = np.random.randn(16000).astype(np.float32)

    with pytest.raises(RuntimeError, match="ASR model not loaded"):
        await engine.transcribe_chunk(audio)


@pytest.mark.asyncio
async def test_get_stats(engine):
    """Test getting engine statistics"""
    stats = engine.get_stats()

    assert 'is_loaded' in stats
    assert 'device' in stats
    assert 'sample_rate' in stats
    assert 'inference_count' in stats


# Integration test - only runs with GPU available
@pytest.mark.skipif(True, reason="Requires GPU and NeMo installation")
@pytest.mark.slow
@pytest.mark.asyncio
async def test_real_model_loading():
    """
    Integration test for real model loading.
    Only runs when explicitly requested with proper GPU setup.
    """
    ASREngine._instance = None
    engine = await ASREngine.get_instance()
    config = Config.load()

    # This will download the model on first run
    await engine.load_model(config)

    assert engine.is_loaded
    assert engine.model is not None
    assert engine.device in ["cuda", "cpu"]
    assert engine.sample_rate == 16000


@pytest.mark.skipif(True, reason="Requires GPU and NeMo installation")
@pytest.mark.slow
@pytest.mark.asyncio
async def test_real_inference():
    """
    Integration test for real inference.
    Only runs when explicitly requested with proper GPU setup.
    """
    ASREngine._instance = None
    engine = await ASREngine.get_instance()
    config = Config.load()

    await engine.load_model(config)

    # Create 1 second of silence
    audio = np.zeros(16000, dtype=np.float32)

    result = await engine.transcribe_chunk(audio)

    assert 'text' in result
    assert 'confidence' in result
    assert 'is_partial' in result
    assert isinstance(result['text'], str)


@pytest.mark.skipif(True, reason="Requires GPU and NeMo installation")
@pytest.mark.slow
@pytest.mark.asyncio
async def test_memory_stability():
    """
    Test that memory doesn't leak over multiple inferences.
    Only runs when explicitly requested with GPU setup.
    """
    import torch

    ASREngine._instance = None
    engine = await ASREngine.get_instance()
    config = Config.load()

    await engine.load_model(config)

    if engine.device != "cuda":
        pytest.skip("Requires CUDA for memory testing")

    initial_memory = torch.cuda.memory_allocated()

    # Run 100 inferences
    for _ in range(100):
        audio = np.random.randn(16000).astype(np.float32)
        await engine.transcribe_chunk(audio)

    torch.cuda.synchronize()
    final_memory = torch.cuda.memory_allocated()

    # Memory should not grow significantly
    memory_growth = final_memory - initial_memory
    assert memory_growth < 10 * 1024 * 1024  # < 10MB growth


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

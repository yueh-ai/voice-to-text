"""Tests for shared Models container."""

import pytest


class TestModels:
    """Tests for the Models container and initialization."""

    def test_models_container_holds_vad_and_asr(self):
        """Models dataclass should hold VAD and ASR model references."""
        from transcription_service.core.models import Models
        from transcription_service.core.vad import VADModel
        from transcription_service.core.mock_asr import MockASRModel

        # Create mock instances
        vad = VADModel(sample_rate=16000, aggressiveness=2)
        asr = MockASRModel(bytes_per_word=12800, latency_ms=50)

        models = Models(vad=vad, asr=asr)

        assert models.vad is vad
        assert models.asr is asr

    def test_init_models_creates_singleton(self):
        """init_models should create and store a singleton."""
        from transcription_service.core.models import init_models, get_models, _reset_models
        from transcription_service.config import Settings

        # Reset any existing state
        _reset_models()

        config = Settings()
        models = init_models(config)

        assert models is not None
        assert models is get_models()

    def test_get_models_raises_before_init(self):
        """get_models should raise RuntimeError if not initialized."""
        from transcription_service.core.models import get_models, _reset_models

        # Reset to uninitialized state
        _reset_models()

        with pytest.raises(RuntimeError, match="not initialized"):
            get_models()

    def test_init_models_creates_vad_model(self):
        """init_models should create a VADModel with correct config."""
        from transcription_service.core.models import init_models, _reset_models
        from transcription_service.core.vad import VADModel
        from transcription_service.config import Settings

        _reset_models()

        config = Settings(sample_rate=16000, vad_aggressiveness=3)
        models = init_models(config)

        assert isinstance(models.vad, VADModel)
        assert models.vad.sample_rate == 16000

    def test_init_models_creates_asr_model(self):
        """init_models should create an ASRModel with correct config."""
        from transcription_service.core.models import init_models, _reset_models
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        _reset_models()

        config = Settings(bytes_per_word=10000, latency_ms=100)
        models = init_models(config)

        assert isinstance(models.asr, MockASRModel)
        assert models.asr.latency_ms == 100

    def test_default_engine_is_mock(self):
        """Default asr_engine setting should be 'mock'."""
        from transcription_service.config import Settings

        config = Settings()
        assert config.asr_engine == "mock"

    def test_init_models_with_mock_config_creates_mock_asr(self):
        """init_models with asr_engine='mock' should create MockASRModel."""
        from transcription_service.core.models import init_models, _reset_models
        from transcription_service.core.mock_asr import MockASRModel
        from transcription_service.config import Settings

        _reset_models()

        config = Settings(asr_engine="mock")
        models = init_models(config)

        assert isinstance(models.asr, MockASRModel)

    def test_init_models_with_nemo_config_raises_without_nemo(self):
        """init_models with asr_engine='nemo' should raise if NeMo is not installed."""
        from transcription_service.core.models import init_models, _reset_models
        from transcription_service.config import Settings

        _reset_models()

        config = Settings(asr_engine="nemo")
        with pytest.raises(RuntimeError, match="nemo-toolkit"):
            init_models(config)

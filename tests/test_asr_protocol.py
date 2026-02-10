"""Tests for the ASR model protocol."""

from transcription_service.core.asr_protocol import ASRModel


class TestASRProtocol:
    """Tests that ASR implementations satisfy the protocol."""

    def test_mock_asr_satisfies_protocol(self):
        """MockASRModel should be a runtime-checkable ASRModel."""
        from transcription_service.core.mock_asr import MockASRModel

        model = MockASRModel()
        assert isinstance(model, ASRModel)

    def test_nemo_asr_has_required_methods(self):
        """NeMoASRModel should have transcribe and transcribe_sync methods."""
        from transcription_service.core.nemo_asr import NeMoASRModel

        model = NeMoASRModel()
        assert hasattr(model, "transcribe")
        assert hasattr(model, "transcribe_sync")
        assert callable(model.transcribe)
        assert callable(model.transcribe_sync)

    def test_nemo_asr_satisfies_protocol(self):
        """NeMoASRModel should be a runtime-checkable ASRModel."""
        from transcription_service.core.nemo_asr import NeMoASRModel

        model = NeMoASRModel()
        assert isinstance(model, ASRModel)

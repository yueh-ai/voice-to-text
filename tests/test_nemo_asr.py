"""Tests for NeMo ASR model (with mocked NeMo internals)."""

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from transcription_service.core.nemo_asr import NeMoASRModel, PerformanceMetrics


class TestPerformanceMetrics:
    """Tests for RTF and latency tracking."""

    def test_initial_stats_are_zero(self):
        """Fresh metrics should have zero values."""
        metrics = PerformanceMetrics()
        stats = metrics.get_stats()
        assert stats["total_inferences"] == 0
        assert stats["avg_rtf"] == 0.0
        assert stats["avg_latency_ms"] == 0.0

    def test_record_updates_stats(self):
        """Recording a measurement should update counters."""
        metrics = PerformanceMetrics()
        metrics.record(audio_duration_s=1.0, inference_duration_s=0.5)
        stats = metrics.get_stats()
        assert stats["total_inferences"] == 1
        assert stats["avg_rtf"] == pytest.approx(0.5, abs=0.01)
        assert stats["avg_latency_ms"] == pytest.approx(500.0, abs=1.0)

    def test_rolling_window_limit(self):
        """History should be capped at maxlen (100)."""
        metrics = PerformanceMetrics()
        for i in range(150):
            metrics.record(audio_duration_s=1.0, inference_duration_s=0.1)
        stats = metrics.get_stats()
        assert stats["total_inferences"] == 150
        assert stats["window_size"] == 100

    def test_zero_audio_duration_skips_rtf(self):
        """Zero-length audio should still record latency but not RTF."""
        metrics = PerformanceMetrics()
        metrics.record(audio_duration_s=0.0, inference_duration_s=0.01)
        assert metrics.avg_rtf == 0.0
        assert metrics.avg_latency > 0.0

    def test_multiple_recordings_average(self):
        """Average should reflect all recorded values."""
        metrics = PerformanceMetrics()
        metrics.record(audio_duration_s=1.0, inference_duration_s=0.2)
        metrics.record(audio_duration_s=1.0, inference_duration_s=0.4)
        assert metrics.avg_rtf == pytest.approx(0.3, abs=0.01)
        assert metrics.avg_latency == pytest.approx(0.3, abs=0.01)


class TestNeMoASRModel:
    """Tests for NeMoASRModel with mocked NeMo internals."""

    def _make_loaded_model(self):
        """Create a NeMoASRModel with a mocked NeMo model loaded."""
        model = NeMoASRModel(warmup=False)
        mock_nemo_model = MagicMock()
        mock_nemo_model.transcribe.return_value = ["hello world"]
        model._model = mock_nemo_model
        model._loaded = True
        model._resolved_device = "cpu"
        return model

    def test_transcribe_sync_returns_text(self):
        """transcribe_sync should return transcribed text."""
        model = self._make_loaded_model()
        # 320 bytes = 160 samples = 10ms at 16kHz
        audio = b"\x00\x01" * 160
        result = model.transcribe_sync(audio)
        assert result == "hello world"

    def test_transcribe_sync_calls_nemo(self):
        """transcribe_sync should call the NeMo model."""
        model = self._make_loaded_model()
        audio = b"\x00\x01" * 160
        model.transcribe_sync(audio)
        model._model.transcribe.assert_called_once()

    def test_transcribe_sync_raises_when_not_loaded(self):
        """transcribe_sync should raise RuntimeError if model not loaded."""
        model = NeMoASRModel()
        with pytest.raises(RuntimeError, match="not loaded"):
            model.transcribe_sync(b"\x00\x01" * 160)

    def test_transcribe_sync_empty_audio_returns_empty(self):
        """Empty audio should return empty string without calling model."""
        model = self._make_loaded_model()
        result = model.transcribe_sync(b"")
        assert result == ""
        model._model.transcribe.assert_not_called()

    def test_transcribe_sync_records_metrics(self):
        """transcribe_sync should record performance metrics."""
        model = self._make_loaded_model()
        audio = b"\x00\x01" * 160
        model.transcribe_sync(audio)
        assert model._metrics.total_inferences == 1

    async def test_async_transcribe_delegates_to_sync(self):
        """async transcribe should delegate to transcribe_sync."""
        model = self._make_loaded_model()
        audio = b"\x00\x01" * 160
        result = await model.transcribe(audio)
        assert result == "hello world"

    def test_transcribe_sync_handles_hypothesis_objects(self):
        """Should handle NeMo Hypothesis objects with .text attribute."""
        model = self._make_loaded_model()
        hypothesis = MagicMock()
        hypothesis.text = "hypothesis text"
        model._model.transcribe.return_value = [hypothesis]
        audio = b"\x00\x01" * 160
        result = model.transcribe_sync(audio)
        assert result == "hypothesis text"

    def test_load_without_nemo_raises_runtime_error(self):
        """load() should raise RuntimeError if nemo is not installed."""
        model = NeMoASRModel()
        with patch.dict("sys.modules", {"nemo": None, "nemo.collections": None, "nemo.collections.asr": None, "nemo.collections.asr.models": None}):
            with pytest.raises(RuntimeError, match="nemo-toolkit"):
                model.load()

    def test_get_stats_includes_model_info(self):
        """get_stats should include model name and device."""
        model = self._make_loaded_model()
        stats = model.get_stats()
        assert stats["model_name"] == "nvidia/parakeet-tdt-0.6b-v3"
        assert stats["device"] == "cpu"
        assert stats["loaded"] is True

    def test_get_stats_unloaded(self):
        """get_stats should show loaded=False when not loaded."""
        model = NeMoASRModel()
        stats = model.get_stats()
        assert stats["loaded"] is False

    def test_cleanup_when_not_loaded(self):
        """cleanup should be safe to call when model is not loaded."""
        model = NeMoASRModel()
        model.cleanup()  # Should not raise
        assert model._model is None

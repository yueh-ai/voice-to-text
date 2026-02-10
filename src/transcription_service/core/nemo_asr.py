"""NeMo ASR model backend.

Wraps NVIDIA NeMo's EncDecRNNTBPEModel for real speech-to-text inference.
Uses lazy imports so torch/nemo are only required when this engine is selected.
"""

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Tracks real-time factor (RTF) and inference latency with a rolling window."""

    _rtf_history: deque = field(default_factory=lambda: deque(maxlen=100))
    _latency_history: deque = field(default_factory=lambda: deque(maxlen=100))
    total_inferences: int = 0

    def record(self, audio_duration_s: float, inference_duration_s: float) -> None:
        """Record a single inference measurement."""
        self.total_inferences += 1
        self._latency_history.append(inference_duration_s)
        if audio_duration_s > 0:
            rtf = inference_duration_s / audio_duration_s
            self._rtf_history.append(rtf)

    @property
    def avg_rtf(self) -> float:
        """Average real-time factor over the rolling window."""
        if not self._rtf_history:
            return 0.0
        return sum(self._rtf_history) / len(self._rtf_history)

    @property
    def avg_latency(self) -> float:
        """Average inference latency in seconds over the rolling window."""
        if not self._latency_history:
            return 0.0
        return sum(self._latency_history) / len(self._latency_history)

    def get_stats(self) -> dict:
        """Return summary statistics."""
        return {
            "total_inferences": self.total_inferences,
            "avg_rtf": round(self.avg_rtf, 4),
            "avg_latency_ms": round(self.avg_latency * 1000, 2),
            "window_size": len(self._rtf_history),
        }


class NeMoASRModel:
    """NeMo-based ASR model implementing the ASRModel protocol.

    Loads NVIDIA's pretrained ASR model and runs inference on PCM audio.
    Thread-safe: GPU inference is serialized via a threading lock.
    """

    def __init__(
        self,
        model_name: str = "nvidia/parakeet-tdt-0.6b-v3",
        device: str = "auto",
        warmup: bool = True,
        sample_rate: int = 16000,
        rtf_warning_threshold: float = 0.9,
    ):
        self.model_name = model_name
        self.device = device
        self.warmup = warmup
        self.sample_rate = sample_rate
        self.rtf_warning_threshold = rtf_warning_threshold

        self._model = None
        self._lock = threading.Lock()
        self._metrics = PerformanceMetrics()
        self._loaded = False

    def load(self) -> None:
        """Load the NeMo model. Must be called before transcription."""
        try:
            import torch
            from nemo.collections.asr.models import EncDecRNNTBPEModel
        except ImportError as e:
            raise RuntimeError(
                "NeMo ASR requires nemo-toolkit and torch. "
                "Install with: uv sync --extra nemo"
            ) from e

        # Determine device
        if self.device == "auto":
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            resolved_device = self.device

        logger.info(
            "Loading NeMo model %s on %s...", self.model_name, resolved_device
        )

        self._model = EncDecRNNTBPEModel.from_pretrained(model_name=self.model_name)
        self._model = self._model.to(resolved_device)
        self._model.eval()
        self._resolved_device = resolved_device

        if self.warmup:
            self._warmup()

        self._loaded = True
        logger.info("NeMo model loaded successfully on %s", resolved_device)

    def _warmup(self) -> None:
        """Run a warmup inference to prime CUDA kernels."""
        import numpy as np

        logger.info("Running warmup inference...")
        silence = np.zeros(self.sample_rate, dtype=np.float32)  # 1 second
        with self._lock:
            self._model.transcribe([silence])
        logger.info("Warmup complete")

    def _transcribe_numpy(self, audio_np) -> str:
        """Run inference on a numpy array. Thread-safe via lock."""
        import numpy as np
        from transcription_service.core.audio_utils import audio_duration_seconds

        duration_s = audio_duration_seconds(audio_np, self.sample_rate)

        with self._lock:
            start = time.monotonic()
            results = self._model.transcribe([audio_np])
            elapsed = time.monotonic() - start

        self._metrics.record(duration_s, elapsed)

        if duration_s > 0 and elapsed / duration_s > self.rtf_warning_threshold:
            logger.warning(
                "High RTF: %.2f (%.3fs audio, %.3fs inference)",
                elapsed / duration_s,
                duration_s,
                elapsed,
            )

        # NeMo returns list of strings or list of Hypothesis objects
        if isinstance(results, list) and len(results) > 0:
            result = results[0]
            if isinstance(result, str):
                return result
            # Hypothesis object
            if hasattr(result, "text"):
                return result.text
        return ""

    def transcribe_sync(self, audio: bytes) -> str:
        """Transcribe PCM audio bytes to text (synchronous).

        Args:
            audio: Raw PCM 16-bit LE audio bytes.

        Returns:
            Transcription text.

        Raises:
            RuntimeError: If model is not loaded.
        """
        if not self._loaded:
            raise RuntimeError("NeMo model not loaded. Call load() first.")

        from transcription_service.core.audio_utils import pcm_bytes_to_float32

        audio_np = pcm_bytes_to_float32(audio)
        if len(audio_np) == 0:
            return ""
        return self._transcribe_numpy(audio_np)

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe PCM audio bytes to text (async).

        Wraps sync inference in run_in_executor to avoid blocking the event loop.

        Args:
            audio: Raw PCM 16-bit LE audio bytes.

        Returns:
            Transcription text.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.transcribe_sync, audio)

    def cleanup(self) -> None:
        """Release GPU memory and model resources."""
        if self._model is not None:
            try:
                import torch

                del self._model
                self._model = None
                self._loaded = False
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("NeMo model cleaned up, GPU memory released")
            except Exception:
                logger.exception("Error during NeMo cleanup")

    def get_stats(self) -> dict:
        """Return model and performance statistics."""
        stats = self._metrics.get_stats()
        stats.update({
            "model_name": self.model_name,
            "device": getattr(self, "_resolved_device", self.device),
            "loaded": self._loaded,
        })
        return stats

import asyncio
import logging
import time
from typing import Dict, Optional
import numpy as np

logger = logging.getLogger(__name__)


class PerformanceMetrics:
    """Track inference performance metrics"""

    def __init__(self):
        self.rtf_history = []
        self.inference_count = 0
        self.total_audio_duration = 0.0
        self.total_inference_time = 0.0

    def record_inference(self, audio_duration: float, inference_time: float):
        """Record an inference timing"""
        rtf = inference_time / audio_duration if audio_duration > 0 else 0.0
        self.rtf_history.append(rtf)

        # Keep only last 100 measurements
        if len(self.rtf_history) > 100:
            self.rtf_history.pop(0)

        self.inference_count += 1
        self.total_audio_duration += audio_duration
        self.total_inference_time += inference_time

    @property
    def average_rtf(self) -> float:
        """Get average real-time factor over recent inferences"""
        if not self.rtf_history:
            return 0.0
        return sum(self.rtf_history) / len(self.rtf_history)

    @property
    def overall_rtf(self) -> float:
        """Get overall RTF since startup"""
        if self.total_audio_duration == 0:
            return 0.0
        return self.total_inference_time / self.total_audio_duration

    def get_stats(self) -> Dict:
        """Get all metrics as dictionary"""
        return {
            "inference_count": self.inference_count,
            "average_rtf": self.average_rtf,
            "overall_rtf": self.overall_rtf,
            "total_audio_hours": self.total_audio_duration / 3600.0
        }


class ASREngine:
    """
    Singleton ASR engine for streaming speech recognition.
    Manages model loading, device allocation, and inference.
    """

    _instance = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.model = None
        self.device = None
        self.is_loaded = False
        self.load_error = None
        self.sample_rate = None
        self.metrics = PerformanceMetrics()
        self.config = None

    @classmethod
    async def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def load_model(self, config):
        """
        Load the ASR model.

        Args:
            config: Configuration object with model settings

        Raises:
            RuntimeError: If model loading fails
        """
        try:
            self.config = config
            self.device = self._detect_device(config)

            logger.info(f"Loading ASR model: {config.model.model_name}")
            logger.info(f"Target device: {self.device}")

            # Import NeMo here to fail gracefully if not installed
            try:
                import nemo.collections.asr as nemo_asr
                import torch
            except ImportError as e:
                raise RuntimeError(
                    f"NeMo toolkit not installed: {e}. "
                    f"Please install with: pip install nemo_toolkit[asr]"
                )

            # Load the model
            logger.info("Downloading/loading model... This may take a while on first run.")
            self.model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
                model_name=config.model.model_name
            )

            # Move to target device
            if self.device == "cuda":
                self.model = self.model.cuda()
            else:
                self.model = self.model.cpu()

            # Set to evaluation mode
            self.model.eval()

            # Get model configuration
            self.sample_rate = self.model.cfg.sample_rate
            logger.info(f"Model sample rate: {self.sample_rate} Hz")

            # Warm-up inference if enabled
            if config.performance.warmup_enabled:
                logger.info("Running warm-up inference...")
                await self._warmup()

            self.is_loaded = True
            logger.info(f"ASR model loaded successfully on {self.device}")

        except FileNotFoundError as e:
            self.load_error = f"Model files not found: {e}"
            logger.error(self.load_error)
            raise RuntimeError(
                f"Failed to load model '{config.model.model_name}'. "
                f"Ensure model files are available or network is accessible."
            )
        except Exception as e:
            self.load_error = str(e)
            logger.error(f"Model loading failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to load ASR model: {e}")

    def _detect_device(self, config) -> str:
        """
        Detect and validate compute device.

        Args:
            config: Configuration with device setting

        Returns:
            Device string ("cuda" or "cpu")

        Raises:
            RuntimeError: If CUDA requested but not available
        """
        try:
            import torch
        except ImportError:
            logger.warning("PyTorch not installed, defaulting to CPU")
            return "cpu"

        device_config = config.model.device

        if device_config == "cpu":
            logger.info("Using CPU (as configured)")
            return "cpu"

        if device_config == "cuda" or device_config == "auto":
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_memory = torch.cuda.get_device_properties(0).total_memory / 1e9
                logger.info(f"CUDA available: {gpu_name} ({gpu_memory:.1f} GB)")
                return "cuda"
            else:
                if device_config == "cuda":
                    raise RuntimeError(
                        "CUDA device requested but not available. "
                        "Please check GPU setup or set device='cpu' in config."
                    )
                logger.warning("CUDA not available, falling back to CPU")
                return "cpu"

        logger.warning(f"Unknown device '{device_config}', defaulting to CPU")
        return "cpu"

    async def _warmup(self):
        """Run warm-up inference to initialize model"""
        try:
            import torch

            # Create dummy audio (1 second)
            dummy_audio = np.random.randn(self.sample_rate).astype(np.float32)

            # Run inference
            start_time = time.time()
            _ = await self.transcribe_chunk(dummy_audio)
            warmup_time = time.time() - start_time

            logger.info(f"Warm-up complete ({warmup_time:.2f}s)")

        except Exception as e:
            logger.warning(f"Warm-up inference failed: {e}")

    async def transcribe_chunk(self, audio: np.ndarray) -> Dict:
        """
        Transcribe an audio chunk.

        Args:
            audio: Numpy array of float32 audio samples

        Returns:
            Dictionary with:
                - text: Transcribed text
                - confidence: Confidence score (placeholder)
                - is_partial: Whether this is a partial result

        Raises:
            RuntimeError: If model not loaded or inference fails
        """
        if not self.is_loaded:
            raise RuntimeError(
                f"ASR model not loaded. Reason: {self.load_error or 'Model not initialized'}"
            )

        try:
            import torch

            audio_duration = len(audio) / self.sample_rate
            start_time = time.time()

            # NeMo transcribe expects list of numpy arrays
            with torch.no_grad():
                hypotheses = self.model.transcribe(
                    audio=[audio],
                    batch_size=1
                )

            text = hypotheses[0] if hypotheses else ""

            inference_time = time.time() - start_time
            self.metrics.record_inference(audio_duration, inference_time)

            # Log warning if RTF is high
            if self.metrics.average_rtf > self.config.performance.rtf_warning_threshold:
                logger.warning(
                    f"High RTF detected: {self.metrics.average_rtf:.3f} "
                    f"(threshold: {self.config.performance.rtf_warning_threshold})"
                )

            logger.debug(
                f"Transcribed {audio_duration:.2f}s audio in {inference_time:.2f}s "
                f"(RTF: {inference_time/audio_duration:.3f})"
            )

            return {
                "text": text,
                "confidence": 1.0,  # NeMo doesn't easily expose confidence
                "is_partial": True
            }

        except Exception as e:
            # Check for CUDA OOM
            if "out of memory" in str(e).lower() or "CUDA" in str(e):
                logger.error("GPU out of memory during inference")
                if self.device == "cuda":
                    try:
                        import torch
                        torch.cuda.empty_cache()
                        logger.info("Cleared GPU cache")
                    except:
                        pass
                raise RuntimeError("GPU memory exhausted during inference")

            logger.error(f"Inference failed: {e}", exc_info=True)
            raise RuntimeError(f"Transcription error: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up ASR engine")

        if self.model is not None and self.device == "cuda":
            try:
                import torch
                # Move model to CPU to free GPU memory
                self.model = self.model.cpu()
                torch.cuda.empty_cache()
                logger.info("GPU memory released")
            except Exception as e:
                logger.warning(f"Error during cleanup: {e}")

        self.model = None
        self.is_loaded = False

    def get_stats(self) -> Dict:
        """Get engine statistics"""
        stats = self.metrics.get_stats()
        stats.update({
            "is_loaded": self.is_loaded,
            "device": self.device,
            "sample_rate": self.sample_rate
        })
        return stats

import numpy as np
import time
import logging
from typing import Optional

from src.config import EndpointingConfig

logger = logging.getLogger(__name__)


class Endpointing:
    """
    Utterance boundary detection (endpointing) for streaming ASR.

    Detects when the speaker has stopped talking to finalize transcriptions.
    Supports both energy-based and VAD-based detection.
    """

    def __init__(self, config: EndpointingConfig):
        self.config = config
        self.strategy = config.strategy

        # State tracking
        self.silence_start: Optional[float] = None
        self.speech_detected = False

        # VAD model (loaded on demand)
        self.vad_model = None

        # Load VAD if requested
        if config.strategy == "vad" and config.vad_enabled:
            self._load_vad_model()

        logger.info(f"Endpointing initialized: strategy={self.strategy}")

    def _load_vad_model(self):
        """Load VAD model (MarbleNet)"""
        try:
            import nemo.collections.asr as nemo_asr

            logger.info("Loading VAD model (MarbleNet)...")
            self.vad_model = nemo_asr.models.EncDecClassificationModel.from_pretrained(
                model_name="nvidia/vad_multilingual_marblenet"
            )
            self.vad_model.eval()
            logger.info("VAD model loaded successfully")

        except Exception as e:
            logger.warning(
                f"Failed to load VAD model: {e}. "
                f"Falling back to energy-based endpointing."
            )
            self.strategy = "energy"
            self.vad_model = None

    def process_audio(self, audio: np.ndarray) -> bool:
        """
        Process audio chunk and detect endpoint.

        Args:
            audio: Audio chunk as numpy array

        Returns:
            True if endpoint detected (utterance boundary), False otherwise
        """
        if self.strategy == "energy":
            return self._energy_based_endpoint(audio)
        elif self.strategy == "vad":
            return self._vad_based_endpoint(audio)
        else:
            logger.warning(f"Unknown strategy '{self.strategy}', using energy-based")
            return self._energy_based_endpoint(audio)

    def _energy_based_endpoint(self, audio: np.ndarray) -> bool:
        """
        Energy-based silence detection using RMS.

        Args:
            audio: Audio chunk as numpy array

        Returns:
            True if endpoint detected
        """
        # Calculate RMS energy
        rms = np.sqrt(np.mean(audio ** 2))

        # Check if below silence threshold
        if rms < self.config.energy_threshold:
            # Silence detected
            if self.silence_start is None:
                # Start tracking silence
                self.silence_start = time.time()
                logger.debug(f"Silence started (RMS: {rms:.6f})")
            else:
                # Check if silence duration exceeds threshold
                silence_duration = time.time() - self.silence_start
                if silence_duration >= self.config.silence_duration:
                    # Long enough silence, trigger endpoint
                    logger.debug(
                        f"Endpoint detected after {silence_duration:.2f}s of silence"
                    )
                    self.silence_start = None
                    self.speech_detected = False
                    return True
        else:
            # Speech detected, reset silence tracking
            if self.silence_start is not None:
                silence_duration = time.time() - self.silence_start
                logger.debug(
                    f"Speech resumed after {silence_duration:.2f}s of silence "
                    f"(RMS: {rms:.6f})"
                )
            self.silence_start = None
            self.speech_detected = True

        return False

    def _vad_based_endpoint(self, audio: np.ndarray) -> bool:
        """
        VAD model-based endpoint detection.

        Args:
            audio: Audio chunk as numpy array

        Returns:
            True if endpoint detected
        """
        if self.vad_model is None:
            # Fallback to energy-based
            return self._energy_based_endpoint(audio)

        try:
            import torch

            # Convert to tensor
            audio_tensor = torch.from_numpy(audio).unsqueeze(0)  # [1, samples]

            # Move to same device as model
            if hasattr(self.vad_model, 'device'):
                audio_tensor = audio_tensor.to(self.vad_model.device)

            # Run VAD inference
            with torch.no_grad():
                logits = self.vad_model(audio_tensor)
                # logits shape: [batch, time, classes] where classes = [background, speech]
                probs = torch.softmax(logits, dim=-1)

                # Average speech probability across time
                speech_prob = probs[0, :, 1].mean().item()

            # Check if speech probability is below threshold (i.e., silence/background)
            if speech_prob < self.config.vad_threshold:
                # Silence/background detected
                if self.silence_start is None:
                    self.silence_start = time.time()
                    logger.debug(f"Silence started (VAD: {speech_prob:.3f})")
                else:
                    silence_duration = time.time() - self.silence_start
                    if silence_duration >= self.config.silence_duration:
                        logger.debug(
                            f"Endpoint detected after {silence_duration:.2f}s "
                            f"(VAD: {speech_prob:.3f})"
                        )
                        self.silence_start = None
                        self.speech_detected = False
                        return True
            else:
                # Speech detected
                if self.silence_start is not None:
                    silence_duration = time.time() - self.silence_start
                    logger.debug(
                        f"Speech resumed after {silence_duration:.2f}s "
                        f"(VAD: {speech_prob:.3f})"
                    )
                self.silence_start = None
                self.speech_detected = True

            return False

        except Exception as e:
            logger.warning(f"VAD inference failed: {e}, falling back to energy-based")
            return self._energy_based_endpoint(audio)

    def reset(self):
        """Reset endpointing state"""
        self.silence_start = None
        self.speech_detected = False
        logger.debug("Endpointing state reset")

    def is_in_silence(self) -> bool:
        """Check if currently in silence period"""
        return self.silence_start is not None

    def get_silence_duration(self) -> float:
        """
        Get current silence duration.

        Returns:
            Duration in seconds, or 0.0 if not in silence
        """
        if self.silence_start is None:
            return 0.0
        return time.time() - self.silence_start

    def get_stats(self) -> dict:
        """Get endpointing statistics"""
        return {
            "strategy": self.strategy,
            "in_silence": self.is_in_silence(),
            "silence_duration": self.get_silence_duration(),
            "speech_detected": self.speech_detected,
            "vad_loaded": self.vad_model is not None
        }

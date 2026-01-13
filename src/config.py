from dataclasses import dataclass, field
from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for ASR model"""
    model_name: str = "nvidia/parakeet-tdt-0.6b-v3"
    device: str = "auto"  # "auto", "cuda", or "cpu"
    cache_dir: Optional[str] = None


@dataclass
class AudioConfig:
    """Configuration for audio processing"""
    sample_rate: int = 16000  # 16kHz
    chunk_duration: float = 1.0  # seconds
    left_context_duration: float = 10.0  # seconds
    right_context_duration: float = 2.0  # seconds
    audio_format: str = "pcm_s16le"  # PCM 16-bit little-endian


@dataclass
class EndpointingConfig:
    """Configuration for endpointing (utterance boundary detection)"""
    strategy: str = "energy"  # "energy" or "vad"
    energy_threshold: float = 0.01  # RMS threshold for silence detection
    silence_duration: float = 0.8  # seconds of silence to trigger endpoint
    vad_threshold: float = 0.5  # VAD confidence threshold (if using VAD)
    vad_enabled: bool = False  # Enable VAD-based endpointing


@dataclass
class PerformanceConfig:
    """Configuration for performance limits and monitoring"""
    max_batch_size: int = 1  # Single user for now
    max_session_duration: int = 3600  # 1 hour max session length (seconds)
    max_buffer_size: int = 160000  # Max audio buffer size (~10 seconds at 16kHz)
    warmup_enabled: bool = True  # Run warmup inference on startup
    rtf_warning_threshold: float = 0.9  # Warn if RTF exceeds this


@dataclass
class Config:
    """Main configuration for the ASR service"""
    model: ModelConfig = field(default_factory=ModelConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    endpointing: EndpointingConfig = field(default_factory=EndpointingConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)

    @classmethod
    def load(cls, config_file: Optional[str] = None) -> 'Config':
        """
        Load configuration from file or environment variables.

        Args:
            config_file: Optional path to YAML config file

        Returns:
            Config instance
        """
        if config_file and os.path.exists(config_file):
            try:
                import yaml
                with open(config_file, 'r') as f:
                    data = yaml.safe_load(f)

                return cls(
                    model=ModelConfig(**data.get('model', {})),
                    audio=AudioConfig(**data.get('audio', {})),
                    endpointing=EndpointingConfig(**data.get('endpointing', {})),
                    performance=PerformanceConfig(**data.get('performance', {}))
                )
            except Exception as e:
                logger.warning(f"Failed to load config from {config_file}: {e}")
                logger.info("Using default configuration")

        # Load from environment variables or use defaults
        model_config = ModelConfig(
            model_name=os.getenv("ASR_MODEL_NAME", "nvidia/parakeet-tdt-0.6b-v3"),
            device=os.getenv("ASR_DEVICE", "auto"),
            cache_dir=os.getenv("ASR_CACHE_DIR")
        )

        audio_config = AudioConfig(
            sample_rate=int(os.getenv("ASR_SAMPLE_RATE", "16000")),
            chunk_duration=float(os.getenv("ASR_CHUNK_DURATION", "1.0")),
            left_context_duration=float(os.getenv("ASR_LEFT_CONTEXT", "10.0")),
            right_context_duration=float(os.getenv("ASR_RIGHT_CONTEXT", "2.0"))
        )

        endpointing_config = EndpointingConfig(
            strategy=os.getenv("ENDPOINTING_STRATEGY", "energy"),
            energy_threshold=float(os.getenv("ENDPOINTING_ENERGY_THRESHOLD", "0.01")),
            silence_duration=float(os.getenv("ENDPOINTING_SILENCE_DURATION", "0.8")),
            vad_enabled=os.getenv("VAD_ENABLED", "false").lower() == "true"
        )

        performance_config = PerformanceConfig(
            max_session_duration=int(os.getenv("MAX_SESSION_DURATION", "3600")),
            max_buffer_size=int(os.getenv("MAX_BUFFER_SIZE", "160000")),
            warmup_enabled=os.getenv("WARMUP_ENABLED", "true").lower() == "true"
        )

        return cls(
            model=model_config,
            audio=audio_config,
            endpointing=endpointing_config,
            performance=performance_config
        )

    def to_dict(self) -> dict:
        """Convert config to dictionary"""
        return {
            'model': {
                'model_name': self.model.model_name,
                'device': self.model.device,
                'cache_dir': self.model.cache_dir
            },
            'audio': {
                'sample_rate': self.audio.sample_rate,
                'chunk_duration': self.audio.chunk_duration,
                'left_context_duration': self.audio.left_context_duration,
                'right_context_duration': self.audio.right_context_duration
            },
            'endpointing': {
                'strategy': self.endpointing.strategy,
                'energy_threshold': self.endpointing.energy_threshold,
                'silence_duration': self.endpointing.silence_duration,
                'vad_enabled': self.endpointing.vad_enabled
            },
            'performance': {
                'max_session_duration': self.performance.max_session_duration,
                'max_buffer_size': self.performance.max_buffer_size,
                'warmup_enabled': self.performance.warmup_enabled
            }
        }

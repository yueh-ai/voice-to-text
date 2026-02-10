"""Configuration management for the transcription service."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Audio assumptions
    sample_rate: int = 16000  # Hz
    sample_width: int = 2  # bytes (16-bit)
    bytes_per_second: int = 32000  # sample_rate * sample_width

    # Text generation
    words_per_second: float = 2.5  # average speaking rate
    bytes_per_word: int = 12800  # bytes_per_second / words_per_second

    # VAD settings
    vad_aggressiveness: int = 2  # 0-3, higher = more aggressive
    vad_frame_ms: int = 20  # frame duration in ms (10, 20, or 30)
    endpointing_ms: int = 300  # silence duration before final

    # Processing
    latency_ms: int = 50  # simulated processing delay

    # ASR engine selection
    asr_engine: str = "mock"  # "mock" or "nemo"
    nemo_model_name: str = "nvidia/parakeet-tdt-0.6b-v3"
    nemo_device: str = "auto"  # "auto", "cuda", "cpu"
    nemo_warmup: bool = True
    nemo_rtf_warning_threshold: float = 0.9

    # Server
    host: str = "0.0.0.0"
    port: int = 8001

    # Session management (Phase 2)
    max_sessions: int = 1000
    session_idle_timeout_seconds: float = 300.0
    session_cleanup_interval_seconds: float = 30.0

    model_config = {"env_prefix": "ASR_"}


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

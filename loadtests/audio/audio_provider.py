"""
Audio provider for load testing with real or synthetic audio.

Loads real PCM clips from clips/ directory (downloaded via download_audio.py)
and serves them to locust users. Falls back to synthetic random PCM if no
clips are available.
"""

import json
import random
import warnings
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # 16-bit
FRAME_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE  # bytes per second

SCRIPT_DIR = Path(__file__).parent
CLIPS_DIR = SCRIPT_DIR / "clips"
MANIFEST_PATH = SCRIPT_DIR / "manifest.json"


@dataclass
class AudioClip:
    """A single audio clip with metadata."""

    pcm_data: bytes
    duration_s: float
    transcript: str = ""
    speaker_id: str = ""
    utterance_id: str = ""
    _chunks_cache: dict[int, list[bytes]] = field(default_factory=dict, repr=False)

    def as_chunks(self, chunk_ms: int = 20) -> list[bytes]:
        """Split PCM data into fixed-size chunks for WebSocket streaming.

        Pads the last chunk to frame boundary if needed.
        """
        if chunk_ms in self._chunks_cache:
            return self._chunks_cache[chunk_ms]

        chunk_bytes = int(SAMPLE_RATE * chunk_ms / 1000) * BYTES_PER_SAMPLE
        chunks = []
        for i in range(0, len(self.pcm_data), chunk_bytes):
            chunk = self.pcm_data[i : i + chunk_bytes]
            # Pad last chunk to frame boundary
            if len(chunk) < chunk_bytes:
                chunk = chunk + b"\x00" * (chunk_bytes - len(chunk))
            chunks.append(chunk)

        self._chunks_cache[chunk_ms] = chunks
        return chunks


class AudioProvider:
    """Provides audio clips for load testing.

    Loads real PCM clips from clips/ directory if available,
    otherwise generates synthetic random PCM data.
    """

    def __init__(self):
        self.clips: list[AudioClip] = []
        self.is_real = False
        self._synthetic_chunks: list[bytes] = []
        self._load_clips()

    def _load_clips(self):
        """Load PCM clips from clips/ directory."""
        if not CLIPS_DIR.exists():
            return

        # Load manifest for metadata
        metadata: dict[str, dict] = {}
        if MANIFEST_PATH.exists():
            try:
                manifest = json.loads(MANIFEST_PATH.read_text())
                for entry in manifest:
                    metadata[entry["filename"]] = entry
            except (json.JSONDecodeError, KeyError):
                warnings.warn("Failed to parse manifest.json, loading clips without metadata")

        # Load PCM files
        pcm_files = sorted(CLIPS_DIR.glob("*.pcm"))
        for pcm_path in pcm_files:
            pcm_data = pcm_path.read_bytes()
            if len(pcm_data) == 0:
                continue

            duration_s = len(pcm_data) / FRAME_BYTES
            meta = metadata.get(pcm_path.name, {})

            self.clips.append(
                AudioClip(
                    pcm_data=pcm_data,
                    duration_s=meta.get("duration_s", round(duration_s, 2)),
                    transcript=meta.get("transcript", ""),
                    speaker_id=meta.get("speaker_id", ""),
                    utterance_id=meta.get("utterance_id", pcm_path.stem),
                )
            )

        if self.clips:
            self.is_real = True

    @property
    def clip_count(self) -> int:
        return len(self.clips)

    @property
    def mode(self) -> str:
        return "real" if self.is_real else "synthetic"

    def get_rest_audio(self) -> bytes:
        """Get a full audio clip for REST endpoint testing."""
        if self.clips:
            return random.choice(self.clips).pcm_data

        # Synthetic fallback: 1-3 seconds of random PCM
        duration_ms = random.randint(1000, 3000)
        return self._generate_synthetic(duration_ms)

    def get_streaming_chunks(self, chunk_ms: int = 20) -> list[bytes]:
        """Get a clip pre-chunked for WebSocket streaming."""
        if self.clips:
            return random.choice(self.clips).as_chunks(chunk_ms)

        # Synthetic fallback: 1-5 seconds of random chunks
        num_chunks = random.randint(50, 250)
        return [self._get_synthetic_chunk(chunk_ms) for _ in range(num_chunks)]

    def _generate_synthetic(self, duration_ms: int) -> bytes:
        """Generate synthetic random PCM audio."""
        samples = int(SAMPLE_RATE * duration_ms / 1000)
        return bytes(random.randint(0, 255) for _ in range(samples * BYTES_PER_SAMPLE))

    def _get_synthetic_chunk(self, chunk_ms: int = 20) -> bytes:
        """Get a pre-generated synthetic chunk (cached for performance)."""
        if not self._synthetic_chunks:
            self._synthetic_chunks = [self._generate_synthetic(20) for _ in range(100)]
        return random.choice(self._synthetic_chunks)


# Module-level singleton
_provider: AudioProvider | None = None


def get_provider() -> AudioProvider:
    """Get the module-level AudioProvider singleton."""
    global _provider
    if _provider is None:
        _provider = AudioProvider()
    return _provider

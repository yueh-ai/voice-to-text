# Phase 1 — Mock Model & Core Service

Detailed implementation plan for Phase 1 of the Scalable Transcription Service.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Text generation | Correlates to audio byte length | Simulates realistic ASR behavior |
| Silence detection | Real VAD (WebRTC) | Lightweight, CPU-only, accurate |
| Response mode | Independent fragments | Less bandwidth, more stateless |
| Audio handling | Use byte length for word count | No actual speech recognition |

---

## Dependencies

```toml
[project]
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "webrtcvad>=2.0.10",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.26.0",
]
```

---

## Project Structure

```
voice-to-text/
├── pyproject.toml
├── src/
│   └── transcription_service/
│       ├── __init__.py
│       ├── main.py              # FastAPI app entry point
│       ├── config.py            # Configuration management
│       ├── api/
│       │   ├── __init__.py
│       │   ├── health.py        # GET /v1/health
│       │   ├── transcribe.py    # POST /v1/transcribe
│       │   └── stream.py        # WS /v1/transcribe/stream
│       └── core/
│           ├── __init__.py
│           ├── vad.py           # WebRTC VAD wrapper
│           ├── mock_asr.py      # Mock ASR model
│           └── text_generator.py # Fake text generation
└── tests/
    ├── __init__.py
    ├── test_health.py
    ├── test_transcribe.py
    ├── test_stream.py
    └── test_mock_asr.py
```

---

## Component Specifications

### 1. Configuration (`config.py`)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Audio assumptions
    sample_rate: int = 16000          # Hz
    sample_width: int = 2             # bytes (16-bit)
    bytes_per_second: int = 32000     # sample_rate * sample_width

    # Text generation
    words_per_second: float = 2.5     # average speaking rate
    bytes_per_word: int = 12800       # bytes_per_second / words_per_second

    # VAD settings
    vad_aggressiveness: int = 2       # 0-3, higher = more aggressive
    endpointing_ms: int = 300         # silence duration before final

    # Processing
    latency_ms: int = 50              # simulated processing delay
    error_rate: float = 0.0           # probability of simulated failure

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_prefix = "ASR_"
```

### 2. VAD Wrapper (`core/vad.py`)

Wraps WebRTC VAD for easy use with streaming audio.

```python
class VADProcessor:
    """
    Processes audio chunks and detects speech/silence.

    WebRTC VAD requirements:
    - Audio must be 16-bit PCM
    - Sample rate: 8000, 16000, 32000, or 48000 Hz
    - Frame duration: 10, 20, or 30 ms
    """

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        ...

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Returns True if chunk contains speech."""
        ...

    def get_frame_duration_ms(self) -> int:
        """Returns the frame duration in milliseconds."""
        ...
```

**WebRTC VAD constraints:**
- Requires specific frame sizes (10ms, 20ms, or 30ms of audio)
- At 16kHz, 16-bit: 320 bytes (10ms), 640 bytes (20ms), 960 bytes (30ms)
- Will need to buffer/split incoming chunks to match

### 3. Text Generator (`core/text_generator.py`)

Generates fake transcription text based on audio duration.

```python
class TextGenerator:
    """
    Generates realistic-looking fake transcription text.

    Uses a vocabulary of common words to create natural-looking output.
    Word count is proportional to audio byte length.
    """

    def __init__(self, bytes_per_word: int = 12800):
        self.bytes_per_word = bytes_per_word
        self.vocabulary = [...]  # Common English words

    def generate(self, audio_bytes: int) -> str:
        """Generate fake text proportional to audio length."""
        word_count = max(1, audio_bytes // self.bytes_per_word)
        return " ".join(random.choices(self.vocabulary, k=word_count))
```

**Vocabulary approach:**
- Use ~500 common English words
- Random selection (not trying to make sentences)
- Sufficient for load testing; obviously fake but structurally valid

### 4. Mock ASR Model (`core/mock_asr.py`)

Combines VAD and text generation into the ASR interface.

```python
@dataclass
class TranscriptResult:
    text: str
    is_final: bool
    duration_ms: float  # processing time

class MockASRModel:
    """
    Mock ASR that uses real VAD and fake text generation.

    Behavior:
    - Receives audio chunks
    - Uses VAD to detect speech vs silence
    - Generates fake text proportional to speech audio length
    - Emits partial results during speech
    - Emits final (empty) result after silence threshold
    """

    def __init__(self, config: Settings):
        self.vad = VADProcessor(config.sample_rate, config.vad_aggressiveness)
        self.text_gen = TextGenerator(config.bytes_per_word)
        self.config = config

        # State
        self._speech_buffer_bytes = 0
        self._silence_duration_ms = 0

    async def process_chunk(self, audio: bytes) -> TranscriptResult | None:
        """
        Process an audio chunk.

        Returns:
        - TranscriptResult with text if speech detected
        - TranscriptResult with is_final=True if silence threshold reached
        - None if silence but threshold not yet reached
        """
        await asyncio.sleep(self.config.latency_ms / 1000)

        if self.vad.is_speech(audio):
            self._speech_buffer_bytes += len(audio)
            self._silence_duration_ms = 0
            text = self.text_gen.generate(len(audio))
            return TranscriptResult(text=text, is_final=False, duration_ms=self.config.latency_ms)
        else:
            self._silence_duration_ms += self._chunk_duration_ms(audio)
            if self._silence_duration_ms >= self.config.endpointing_ms:
                self._reset()
                return TranscriptResult(text="", is_final=True, duration_ms=self.config.latency_ms)
            return None

    def _reset(self):
        self._speech_buffer_bytes = 0
        self._silence_duration_ms = 0
```

### 5. API Endpoints

#### Health (`api/health.py`)

```
GET /v1/health

Response:
{
    "status": "ok",
    "version": "0.1.0"
}
```

#### REST Transcription (`api/transcribe.py`)

```
POST /v1/transcribe
Content-Type: audio/wav (or multipart/form-data)
Body: <audio bytes>

Response:
{
    "text": "fake transcription text here",
    "duration_ms": 150
}
```

- Processes entire audio file at once
- Returns single fake transcription based on file size
- No VAD (just byte length → word count)

#### WebSocket Streaming (`api/stream.py`)

```
WS /v1/transcribe/stream

Client → Server:
{ "type": "audio", "data": "<base64 PCM audio>" }
{ "type": "stop" }

Server → Client:
{ "type": "partial", "text": "hello world" }
{ "type": "final" }
{ "type": "error", "message": "...", "code": "..." }
```

**Connection lifecycle:**
1. Client connects
2. Server creates MockASRModel instance for session
3. Client sends audio chunks
4. Server processes with VAD, returns partials
5. On silence threshold → server sends final
6. Client sends stop OR disconnects → cleanup

---

## Implementation Order

### Step 1: Project Scaffolding
- Create directory structure
- Set up `pyproject.toml` with dependencies
- Create `config.py` with settings
- Verify `uv sync` works

### Step 2: Health Endpoint
- Create FastAPI app in `main.py`
- Add `/v1/health` endpoint
- Test server starts and responds

### Step 3: VAD Wrapper
- Implement `VADProcessor` class
- Handle frame size requirements
- Unit test with sample audio bytes

### Step 4: Text Generator
- Implement `TextGenerator` class
- Create word vocabulary
- Unit test byte-to-word correlation

### Step 5: Mock ASR Model
- Implement `MockASRModel` combining VAD + text gen
- Handle state management (silence duration tracking)
- Unit test the full flow

### Step 6: REST Endpoint
- Implement `POST /v1/transcribe`
- Accept audio file upload
- Return fake transcription
- Integration test

### Step 7: WebSocket Endpoint
- Implement `WS /v1/transcribe/stream`
- Handle message protocol
- Wire up MockASRModel
- Integration test with mock client

---

## Exit Criteria

- [x] `GET /v1/health` returns 200
- [x] `POST /v1/transcribe` accepts audio, returns fake text
- [x] `WS /v1/transcribe/stream` accepts chunks, returns partials and finals
- [x] VAD correctly distinguishes speech from silence
- [x] Service starts in < 2 seconds
- [x] All tests pass

---

## Open Questions (Resolved)

1. **Audio format for WebSocket**: Base64 encoded PCM, or raw binary frames?
   - **Decision**: Base64 for JSON safety, optimize later if needed

2. **Frame size handling**: Client sends arbitrary chunk sizes, VAD needs specific sizes
   - **Decision**: Buffer server-side for flexibility

3. **Error simulation**: How should `error_rate` work?
   - **Decision**: Deferred to Phase 2

---

## Phase 1.5: Shared Model Architecture (Completed 2026-01-31)

After Phase 1 completion, the architecture was refactored to separate model weights (shared singleton) from inference state (per-user session). See `changes_plan.md` for the detailed plan and `progress.md` for current status.

**Key Changes:**
- Split `VADProcessor` → `VADModel` (shared) + `VADSession` (per-user)
- Made `MockASRModel` stateless
- Added `Models` container with `init_models()` / `get_models()`
- Added `TranscriptionSession` for per-user state
- Updated `main.py` with FastAPI lifespan for model initialization
- Updated endpoints to use dependency injection

**New Tests Added:** 22 tests for the refactored architecture (31 total)

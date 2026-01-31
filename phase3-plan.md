# Phase 3: Performance & Scaling Patterns — Detailed Plan

## Objective

Make the transcription service fast and horizontally scalable. A single worker should handle 500+ concurrent connections, and adding workers should increase capacity linearly.

---

## Prerequisites

- Phase 2 complete (session management working)
- Basic load testing capability (manual or scripted)

---

## Deliverables Overview

| Component | Description |
|-----------|-------------|
| Async audit | Remove all blocking calls |
| Backpressure handling | Protect server from slow clients |
| Audio buffer limits | Bound memory per session |
| Externalized session state | Redis adapter (in-memory fallback) |
| Multi-worker support | Uvicorn workers + shared state |
| Configuration system | Environment-driven tuning |

---

## Implementation Tasks

### 3.1 Async Audit & Optimization

**Goal**: Ensure no blocking calls in the request path.

#### 3.1.1 Audit Current Code

Review all code paths for blocking operations:

```python
# BLOCKING - these must be fixed
time.sleep()           # Use asyncio.sleep()
requests.get()         # Use httpx.AsyncClient
open().read()          # Use aiofiles
json.loads() on huge   # Stream with ijson if needed
```

Checklist:
- [ ] `MockASRModel.transcribe_sync()` - Currently sync, evaluate if problematic
- [ ] VAD processing - CPU-bound, may need thread pool
- [ ] Session creation/cleanup - Should be fast, verify
- [ ] Logging - Ensure not blocking on I/O

#### 3.1.2 Thread Pool for CPU-Bound Work

VAD processing is CPU-bound. For high concurrency, offload to thread pool:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

class TranscriptionSession:
    _executor = ThreadPoolExecutor(max_workers=4)

    async def process_chunk(self, audio: bytes) -> TranscriptResult:
        loop = asyncio.get_event_loop()

        # Run CPU-bound VAD in thread pool
        is_speech = await loop.run_in_executor(
            self._executor,
            self.vad_session.is_speech,
            audio
        )

        # Rest of processing...
```

Configuration:
```python
class Settings(BaseSettings):
    vad_thread_pool_size: int = 4

    class Config:
        env_prefix = "ASR_"
```

#### 3.1.3 Async Model Interface

Ensure the ASR model interface is async-ready:

```python
class ASRModel(Protocol):
    async def transcribe(self, audio: bytes) -> str:
        """Async transcription - real models will need this."""
        ...

class MockASRModel:
    async def transcribe(self, audio: bytes) -> str:
        # Simulate async processing delay
        await asyncio.sleep(self.latency_ms / 1000)
        return self.text_gen.generate(len(audio))

    def transcribe_sync(self, audio: bytes) -> str:
        """Sync version for compatibility."""
        return self.text_gen.generate(len(audio))
```

---

### 3.2 Backpressure Handling

**Goal**: Protect the server when clients can't keep up with responses.

#### 3.2.1 The Problem

A slow client (bad network, overloaded device) can't receive WebSocket messages as fast as we send them. Without backpressure:
- Send buffers grow unbounded
- Memory usage spikes
- Eventually, OOM or connection timeout

#### 3.2.2 WebSocket Send Queue Limits

Implement send queue monitoring:

```python
from dataclasses import dataclass, field
from collections import deque
from typing import Deque
import asyncio

@dataclass
class ClientConnection:
    websocket: WebSocket
    send_queue: Deque[dict] = field(default_factory=deque)
    max_queue_size: int = 100
    _send_task: asyncio.Task | None = None

    async def send(self, message: dict) -> bool:
        """Queue a message for sending. Returns False if queue is full."""
        if len(self.send_queue) >= self.max_queue_size:
            return False  # Client too slow

        self.send_queue.append(message)

        if self._send_task is None or self._send_task.done():
            self._send_task = asyncio.create_task(self._drain_queue())

        return True

    async def _drain_queue(self):
        """Send queued messages to client."""
        while self.send_queue:
            message = self.send_queue.popleft()
            try:
                await asyncio.wait_for(
                    self.websocket.send_json(message),
                    timeout=5.0  # Don't wait forever
                )
            except asyncio.TimeoutError:
                # Client too slow, drop message
                logger.warning("Send timeout, dropping message")
```

#### 3.2.3 Slow Client Detection

Track send latency and disconnect problematic clients:

```python
@dataclass
class ClientConnection:
    slow_send_count: int = 0
    slow_send_threshold: int = 10

    async def send(self, message: dict) -> bool:
        if len(self.send_queue) >= self.max_queue_size:
            self.slow_send_count += 1

            if self.slow_send_count >= self.slow_send_threshold:
                logger.warning(f"Client too slow, disconnecting")
                await self.close(code=1008, reason="Client too slow")
                return False

            # Drop this message but keep connection for now
            return False

        self.slow_send_count = 0  # Reset on successful queue
        # ...
```

Configuration:
```python
class Settings(BaseSettings):
    max_send_queue_size: int = 100
    send_timeout_seconds: float = 5.0
    slow_client_threshold: int = 10
```

---

### 3.3 Audio Buffer Limits

**Goal**: Prevent memory exhaustion from large audio uploads.

#### 3.3.1 Per-Message Size Limit

Reject oversized audio chunks:

```python
MAX_CHUNK_SIZE = 64 * 1024  # 64KB per chunk (plenty for 100ms of audio)

async def handle_audio_chunk(websocket: WebSocket, data: str, session: TranscriptionSession):
    try:
        audio = base64.b64decode(data)
    except Exception:
        await send_error(websocket, "Invalid base64", "INVALID_AUDIO")
        return

    if len(audio) > MAX_CHUNK_SIZE:
        await send_error(websocket, f"Chunk too large: {len(audio)} > {MAX_CHUNK_SIZE}", "CHUNK_TOO_LARGE")
        return

    result = await session.process_chunk(audio)
    # ...
```

#### 3.3.2 Per-Session Buffer Limit

Limit accumulated audio in VAD buffer:

```python
class VADSession:
    MAX_BUFFER_SIZE = 32 * 1024  # 32KB max buffer

    def is_speech(self, audio: bytes) -> bool:
        if len(self._buffer) + len(audio) > self.MAX_BUFFER_SIZE:
            # Buffer overflow - process what we have and discard old data
            self._buffer = self._buffer[-(self.MAX_BUFFER_SIZE // 2):]
            logger.warning("VAD buffer overflow, discarding old audio")

        self._buffer.extend(audio)
        # ...
```

#### 3.3.3 Rate Limiting (Optional)

Limit audio ingestion rate per session:

```python
@dataclass
class RateLimiter:
    max_bytes_per_second: int = 32000 * 2  # 32kHz 16-bit = 64KB/s
    window_seconds: float = 1.0
    _bytes_this_window: int = 0
    _window_start: float = 0.0

    def check(self, num_bytes: int) -> bool:
        now = time.monotonic()

        if now - self._window_start > self.window_seconds:
            self._window_start = now
            self._bytes_this_window = 0

        if self._bytes_this_window + num_bytes > self.max_bytes_per_second:
            return False

        self._bytes_this_window += num_bytes
        return True
```

---

### 3.4 Externalized Session State

**Goal**: Enable horizontal scaling by sharing session state across workers.

#### 3.4.1 Session State Interface

Define what needs to be shared:

```python
from abc import ABC, abstractmethod
from typing import Optional

@dataclass
class SessionState:
    session_id: str
    state: str  # "created", "active", "closing", "closed"
    created_at: datetime
    last_activity_at: datetime
    worker_id: str  # Which worker owns this session
    metrics: dict  # Serializable metrics

class SessionStore(ABC):
    @abstractmethod
    async def create(self, session_id: str, state: SessionState) -> bool:
        """Create session. Returns False if already exists."""
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Optional[SessionState]:
        """Get session state."""
        ...

    @abstractmethod
    async def update(self, session_id: str, state: SessionState) -> bool:
        """Update session. Returns False if not found."""
        ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """Delete session."""
        ...

    @abstractmethod
    async def list_by_worker(self, worker_id: str) -> list[SessionState]:
        """List sessions owned by a worker."""
        ...

    @abstractmethod
    async def count_active(self) -> int:
        """Count non-closed sessions across all workers."""
        ...
```

#### 3.4.2 In-Memory Implementation (Default)

For single-worker or demo scenarios:

```python
class InMemorySessionStore(SessionStore):
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, state: SessionState) -> bool:
        async with self._lock:
            if session_id in self._sessions:
                return False
            self._sessions[session_id] = state
            return True

    async def get(self, session_id: str) -> Optional[SessionState]:
        return self._sessions.get(session_id)

    # ... other methods
```

#### 3.4.3 Redis Implementation

For multi-worker deployments:

```python
import redis.asyncio as redis
import json

class RedisSessionStore(SessionStore):
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._redis = redis.from_url(redis_url)
        self._prefix = "session:"
        self._ttl = 3600  # 1 hour TTL for safety

    async def create(self, session_id: str, state: SessionState) -> bool:
        key = f"{self._prefix}{session_id}"
        # NX = only set if not exists
        result = await self._redis.set(
            key,
            state.to_json(),
            nx=True,
            ex=self._ttl
        )
        return result is not None

    async def get(self, session_id: str) -> Optional[SessionState]:
        key = f"{self._prefix}{session_id}"
        data = await self._redis.get(key)
        if data is None:
            return None
        return SessionState.from_json(data)

    async def update(self, session_id: str, state: SessionState) -> bool:
        key = f"{self._prefix}{session_id}"
        # XX = only set if exists
        result = await self._redis.set(
            key,
            state.to_json(),
            xx=True,
            ex=self._ttl
        )
        return result is not None

    async def count_active(self) -> int:
        # Use Redis SCAN to count keys
        count = 0
        async for key in self._redis.scan_iter(f"{self._prefix}*"):
            data = await self._redis.get(key)
            if data:
                state = SessionState.from_json(data)
                if state.state not in ("closing", "closed"):
                    count += 1
        return count
```

#### 3.4.4 Store Factory

Select implementation based on configuration:

```python
def create_session_store(settings: Settings) -> SessionStore:
    if settings.redis_url:
        logger.info(f"Using Redis session store: {settings.redis_url}")
        return RedisSessionStore(settings.redis_url)
    else:
        logger.info("Using in-memory session store")
        return InMemorySessionStore()
```

Configuration:
```python
class Settings(BaseSettings):
    redis_url: str | None = None  # None = use in-memory

    class Config:
        env_prefix = "ASR_"
```

---

### 3.5 Multi-Worker Support

**Goal**: Run multiple worker processes that share load.

#### 3.5.1 Worker Identity

Each worker needs a unique ID:

```python
import os
import uuid

class WorkerInfo:
    def __init__(self):
        self.worker_id = os.environ.get("WORKER_ID", str(uuid.uuid4())[:8])
        self.pid = os.getpid()

    def __str__(self):
        return f"worker-{self.worker_id}-pid-{self.pid}"
```

#### 3.5.2 Uvicorn Multi-Worker Setup

```python
# main.py
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        workers=4,  # Multiple worker processes
        loop="uvloop",  # Faster event loop
        http="httptools",  # Faster HTTP parsing
    )
```

Or via command line:
```bash
uvicorn app:app --workers 4 --loop uvloop --http httptools
```

#### 3.5.3 WebSocket Sticky Sessions

**Important**: WebSocket connections must stay on the same worker for their lifetime. This requires load balancer configuration.

For nginx:
```nginx
upstream backend {
    ip_hash;  # Sticky sessions based on client IP
    server worker1:8000;
    server worker2:8000;
    server worker3:8000;
}

# Or use a cookie for more control
upstream backend {
    server worker1:8000;
    server worker2:8000;
    sticky cookie srv_id expires=1h;
}
```

For demo without load balancer, single worker per port:
```bash
# Terminal 1
ASR_WORKER_ID=w1 uvicorn app:app --port 8001

# Terminal 2
ASR_WORKER_ID=w2 uvicorn app:app --port 8002
```

#### 3.5.4 Session Affinity

Sessions are owned by the worker that created them:

```python
class SessionManager:
    def __init__(self, store: SessionStore, worker_info: WorkerInfo):
        self._store = store
        self._worker = worker_info
        self._local_sessions: dict[str, TranscriptionSession] = {}

    async def create_session(self) -> TranscriptionSession:
        # Check global limit
        global_count = await self._store.count_active()
        if global_count >= self.config.max_global_sessions:
            raise SessionLimitExceeded("Global session limit reached")

        # Check per-worker limit
        local_count = len(self._local_sessions)
        if local_count >= self.config.max_sessions_per_worker:
            raise SessionLimitExceeded("Worker session limit reached")

        # Create session
        session = TranscriptionSession(...)

        # Register in store
        state = SessionState(
            session_id=session.session_id,
            state="created",
            worker_id=self._worker.worker_id,
            ...
        )
        await self._store.create(session.session_id, state)

        # Keep local reference for processing
        self._local_sessions[session.session_id] = session

        return session
```

---

### 3.6 Configuration System

**Goal**: Make all tuning parameters configurable via environment.

#### 3.6.1 Comprehensive Settings

```python
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    # Session limits
    max_sessions_per_worker: int = 500
    max_global_sessions: int = 2000
    idle_timeout_seconds: int = 300

    # Audio processing
    endpointing_ms: int = 300
    max_chunk_size_bytes: int = 65536
    max_audio_rate_bytes_per_sec: int = 64000

    # Backpressure
    max_send_queue_size: int = 100
    send_timeout_seconds: float = 5.0
    slow_client_threshold: int = 10

    # Threading
    vad_thread_pool_size: int = 4

    # External services
    redis_url: Optional[str] = None

    # Mock model
    mock_latency_ms: int = 50
    mock_words_per_chunk: int = 2

    class Config:
        env_prefix = "ASR_"
        env_file = ".env"
```

#### 3.6.2 Configuration Validation

```python
from pydantic import field_validator

class Settings(BaseSettings):
    # ...

    @field_validator("max_sessions_per_worker")
    @classmethod
    def validate_max_sessions(cls, v):
        if v < 1:
            raise ValueError("max_sessions_per_worker must be >= 1")
        if v > 10000:
            raise ValueError("max_sessions_per_worker > 10000 is not recommended")
        return v

    @field_validator("endpointing_ms")
    @classmethod
    def validate_endpointing(cls, v):
        if v < 100:
            raise ValueError("endpointing_ms < 100 will cause excessive fragmentation")
        if v > 5000:
            raise ValueError("endpointing_ms > 5000 will cause poor UX")
        return v
```

#### 3.6.3 Configuration Logging

On startup, log effective configuration:

```python
@app.on_event("startup")
async def log_config():
    settings = get_settings()
    logger.info("Starting with configuration:")
    for key, value in settings.model_dump().items():
        # Mask sensitive values
        if "password" in key.lower() or "secret" in key.lower():
            value = "****"
        logger.info(f"  {key}: {value}")
```

---

## File Structure

```
src/voice_to_text/
├── config.py              # Settings class (expand existing)
├── models.py              # ASR model interface (expand)
├── session.py             # TranscriptionSession (expand)
├── session_manager.py     # SessionManager (expand)
├── store/
│   ├── __init__.py
│   ├── base.py            # SessionStore ABC
│   ├── memory.py          # InMemorySessionStore
│   └── redis.py           # RedisSessionStore
├── connection.py          # ClientConnection with backpressure (new)
├── worker.py              # WorkerInfo (new)
└── routes/
    └── websocket.py       # Update to use new components
```

---

## Testing Strategy

### Unit Tests

- `test_backpressure.py`: Queue limits, slow client detection
- `test_buffer_limits.py`: Chunk size, VAD buffer overflow
- `test_session_store.py`: Both in-memory and Redis implementations
- `test_rate_limiter.py`: Rate limiting logic

### Integration Tests

- Multi-session concurrent processing
- Worker restart with Redis state persistence
- Graceful degradation under load

### Load Tests (Preparation for Phase 4)

- 500 concurrent connections per worker
- Measure p99 latency
- Memory usage over time

---

## Exit Criteria

| Criteria | Measurement |
|----------|-------------|
| Single worker handles 500+ connections | Load test with 500 simulated clients |
| Adding workers increases capacity linearly | 2 workers handle ~1000, 4 handle ~2000 |
| p99 latency < 100ms | Measure during load test |
| No memory leaks | Memory stable over 1 hour at load |
| Backpressure works | Slow clients don't crash server |
| Configuration works | All settings controllable via env vars |

---

## Dependencies

```toml
# pyproject.toml additions
[project.dependencies]
# ... existing ...
redis = "^5.0"       # Redis client (optional runtime)
uvloop = "^0.19"     # Faster event loop (optional)
httptools = "^0.6"   # Faster HTTP parsing (optional)

[project.optional-dependencies]
redis = ["redis>=5.0"]
performance = ["uvloop>=0.19", "httptools>=0.6"]
```

---

## Rollout Plan

1. **Week 1**: Async audit + backpressure handling
2. **Week 2**: Buffer limits + rate limiting
3. **Week 3**: Session store abstraction + Redis implementation
4. **Week 4**: Multi-worker support + configuration system
5. **Week 5**: Integration testing + performance validation

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Redis adds latency | Higher p99 | Use connection pooling, local cache for hot paths |
| WebSocket sticky sessions complex | Users get errors | Document load balancer config clearly |
| Thread pool contention | Increased latency | Tune pool size, monitor queue depth |
| Configuration overload | Ops confusion | Sensible defaults, document key settings |

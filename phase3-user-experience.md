# User Experience Scenarios — Phase 3

This document explains the user scenarios that drive Phase 3's performance and scaling design. Each scenario describes a real situation, the problem it creates, and how the code handles it.

---

## 1. User on a Flaky Mobile Connection

### The Situation

Carlos is transcribing a voice memo while walking through a subway station. His phone keeps switching between cellular and WiFi, with signal strength varying wildly. Sometimes he has 4G, sometimes he's in a dead zone, sometimes he's on slow station WiFi.

The server is sending transcription results back, but Carlos's phone can't always receive them quickly.

### The Problem

Without protection, when Carlos's connection slows down:
- The server keeps generating transcriptions and trying to send them
- Each `websocket.send_json()` call queues data in the OS send buffer
- The buffer grows and grows because Carlos can't receive fast enough
- Server memory climbs
- Eventually, the server runs out of memory or the connection times out catastrophically

This is called the "slow consumer" problem, and it's one of the most common ways WebSocket servers fail under real-world conditions.

### How the Code Handles It

**Send queue with bounded size:**

```python
@dataclass
class ClientConnection:
    websocket: WebSocket
    send_queue: Deque[dict] = field(default_factory=deque)
    max_queue_size: int = 100  # Never more than 100 pending messages

    async def send(self, message: dict) -> bool:
        if len(self.send_queue) >= self.max_queue_size:
            return False  # Queue full - can't accept more

        self.send_queue.append(message)
        # ... drain queue asynchronously ...
        return True
```

When Carlos's connection slows:
1. Server generates transcription result
2. Tries to queue it: `connection.send(result)`
3. If queue is full (100 messages waiting), returns `False`
4. Server drops this particular message rather than buffering infinitely

**Why dropping messages is okay:**

For transcription, dropping an occasional partial result isn't catastrophic:
- The next partial will include more context
- Finals are more important than partials
- A slightly choppy transcript is better than a crashed server

**Tracking slow clients:**

```python
slow_send_count: int = 0
slow_send_threshold: int = 10

async def send(self, message: dict) -> bool:
    if len(self.send_queue) >= self.max_queue_size:
        self.slow_send_count += 1

        if self.slow_send_count >= self.slow_send_threshold:
            # This client is consistently too slow
            await self.close(code=1008, reason="Client too slow")
            return False

        return False  # Drop message but keep trying

    self.slow_send_count = 0  # Good send, reset counter
    # ...
```

Carlos's experience:
- Brief slow period (queue fills, some partials dropped): Transcript has minor gaps
- Sustained slow connection (10+ consecutive full queues): Disconnected with clear error
- Client app shows: "Connection unstable. Reconnect?"

### The Balance

- **Memory bounded**: Queue never exceeds 100 messages (~100KB worst case)
- **Graceful degradation**: Occasional drops are invisible to user
- **Clear failure mode**: Persistently slow clients get disconnected cleanly
- **Configurable**: Ops can tune `max_send_queue_size` and `slow_send_threshold`

---

## 2. Malicious or Buggy Client Sending Huge Audio Chunks

### The Situation

Someone (or some buggy client app) connects and starts sending 10MB audio chunks instead of normal ~4KB chunks. Maybe it's:
- A bug where the client concatenates hours of audio before sending
- An attacker trying to exhaust server memory
- A misconfigured client sending the wrong format

### The Problem

If the server naively accepts whatever data clients send:
- Single request consumes 10MB of memory
- A few such clients exhaust available RAM
- Server OOMs, everyone's sessions die
- DOS achieved with minimal effort

### How the Code Handles It

**Per-chunk size limit:**

```python
MAX_CHUNK_SIZE = 64 * 1024  # 64KB max

async def handle_audio_chunk(websocket: WebSocket, data: str, session):
    try:
        audio = base64.b64decode(data)
    except Exception:
        await send_error(websocket, "Invalid base64", "INVALID_AUDIO")
        return

    if len(audio) > MAX_CHUNK_SIZE:
        await send_error(
            websocket,
            f"Chunk too large: {len(audio)} > {MAX_CHUNK_SIZE}",
            "CHUNK_TOO_LARGE"
        )
        return

    # Only process if size is reasonable
    result = await session.process_chunk(audio)
```

**Why 64KB?**

Let's do the math:
- 16kHz sample rate, 16-bit audio = 32,000 bytes/second
- 100ms of audio = 3,200 bytes
- 1 second of audio = 32,000 bytes
- 64KB = 2 seconds of audio

No legitimate real-time streaming client needs to send more than 2 seconds at once. If they are, something is wrong.

**VAD buffer protection:**

Even if chunks are small, they accumulate in the VAD buffer:

```python
class VADSession:
    MAX_BUFFER_SIZE = 32 * 1024  # 32KB max

    def is_speech(self, audio: bytes) -> bool:
        if len(self._buffer) + len(audio) > self.MAX_BUFFER_SIZE:
            # Overflow - keep recent audio, discard old
            self._buffer = self._buffer[-(self.MAX_BUFFER_SIZE // 2):]
            logger.warning("VAD buffer overflow, discarding old audio")

        self._buffer.extend(audio)
        # ...
```

### What the Attacker Experiences

1. Connect to WebSocket - succeeds
2. Send 10MB chunk - immediately rejected with `CHUNK_TOO_LARGE`
3. Send many small chunks rapidly - VAD buffer discards old data, stays bounded
4. Server continues running normally for legitimate users

### The Design Principle

**Fail fast, fail cheaply.** Check input validity before doing expensive work:
- Size check: O(1), happens before base64 decode
- Rejection: Single small message, connection stays open for retry
- No resource allocation for invalid requests

---

## 3. High-Traffic Event (Product Launch)

### The Situation

TechCorp is doing a live product launch. Their CEO is on stage, and 5,000 viewers want live captions. They have 5 server instances behind a load balancer, each configured for 1,000 max sessions.

At 10:00 AM exactly, the stream goes live and 5,000 people click "Enable captions."

### The Problem

Without coordination between workers:
- Each worker thinks it can accept 1,000 sessions
- All 5 workers accept connections simultaneously
- Total: 5,000 sessions might try to start
- But if workers can't share state, they can't enforce global limits
- Memory usage could exceed what the cluster can handle
- Workers might be unevenly loaded (one has 900, another has 100)

### How the Code Handles It

**Externalized session state with Redis:**

```python
class RedisSessionStore(SessionStore):
    async def count_active(self) -> int:
        """Count sessions across ALL workers."""
        count = 0
        async for key in self._redis.scan_iter("session:*"):
            data = await self._redis.get(key)
            if data:
                state = SessionState.from_json(data)
                if state.state not in ("closing", "closed"):
                    count += 1
        return count
```

**Two-level limits:**

```python
class SessionManager:
    async def create_session(self) -> TranscriptionSession:
        # Level 1: Global limit across all workers
        global_count = await self._store.count_active()
        if global_count >= self.config.max_global_sessions:
            raise SessionLimitExceeded("Global session limit reached")

        # Level 2: Per-worker limit (local memory constraint)
        local_count = len(self._local_sessions)
        if local_count >= self.config.max_sessions_per_worker:
            raise SessionLimitExceeded("Worker session limit reached")

        # Both limits passed - create session
        session = TranscriptionSession(...)
        # ...
```

Configuration:
```python
max_sessions_per_worker: int = 1000   # Memory constraint
max_global_sessions: int = 4500       # Cluster capacity (90% of 5×1000)
```

**Worker identity:**

Each session knows which worker owns it:

```python
state = SessionState(
    session_id=session.session_id,
    worker_id=self._worker.worker_id,  # "worker-a1b2"
    # ...
)
await self._store.create(session.session_id, state)
```

### What Viewers Experience

**First 4,500 viewers:**
- Click "Enable captions"
- Connection succeeds
- Captions start flowing

**Viewers 4,501-5,000:**
- Click "Enable captions"
- WebSocket connects
- Immediately receive error: "Service at capacity, please try again"
- Client shows: "Live captions temporarily unavailable"

**Load distribution:**

The load balancer (with proper sticky session config) distributes connections:
- Worker 1: ~900 sessions
- Worker 2: ~920 sessions
- Worker 3: ~880 sessions
- Worker 4: ~910 sessions
- Worker 5: ~890 sessions

Reasonably balanced, each worker well under its 1,000 limit.

### Why Redis?

Without shared state, each worker operates in isolation:
- Can't enforce global limits
- Can't see cluster-wide metrics
- Can't implement features like "find least loaded worker"

With Redis:
- Global session count is accurate
- Any worker can check cluster capacity
- Workers can be added/removed dynamically
- Session metadata survives worker restarts

### The Fallback

For simpler deployments (single worker, dev environments):

```python
def create_session_store(settings: Settings) -> SessionStore:
    if settings.redis_url:
        return RedisSessionStore(settings.redis_url)
    else:
        # Single-worker mode - just use memory
        return InMemorySessionStore()
```

No Redis dependency for basic usage.

---

## 4. CPU-Bound VAD During High Concurrency

### The Situation

A customer support center has 200 agents, all using the transcription service simultaneously. Each agent is on a call, streaming audio continuously.

The server is handling 200 concurrent audio streams. Every chunk needs VAD processing to detect speech vs. silence.

### The Problem

VAD processing is CPU-bound:
- Analyzing 20ms of audio takes ~1-2ms of CPU time
- 200 sessions × 50 chunks/second = 10,000 VAD calls per second
- Python's GIL means only one thread runs Python code at a time
- All that VAD work blocks the async event loop
- WebSocket message handling gets delayed
- Latency spikes, timeouts occur

### How the Code Handles It

**Thread pool for CPU-bound work:**

```python
from concurrent.futures import ThreadPoolExecutor
import asyncio

class TranscriptionSession:
    # Shared across all sessions - limited workers
    _executor = ThreadPoolExecutor(max_workers=4)

    async def process_chunk(self, audio: bytes) -> TranscriptResult:
        loop = asyncio.get_event_loop()

        # Run VAD in thread pool - doesn't block event loop
        is_speech = await loop.run_in_executor(
            self._executor,
            self.vad_session.is_speech,
            audio
        )

        # Back on event loop for async operations
        if is_speech:
            text = await self.models.asr.transcribe(audio)
            return TranscriptResult(text=text, ...)
```

**How it works:**

1. Audio chunk arrives on WebSocket (async, event loop)
2. `process_chunk()` called (async)
3. VAD work submitted to thread pool
4. Event loop continues handling other WebSockets while VAD runs
5. Thread pool completes VAD, resumes the coroutine
6. ASR transcription (also async) proceeds

**Why 4 workers?**

```python
vad_thread_pool_size: int = 4
```

- Too few: VAD becomes a bottleneck, queue builds up
- Too many: Context switching overhead, memory usage
- 4 is a reasonable default for a 4-core machine
- Configurable via `ASR_VAD_THREAD_POOL_SIZE=8` for bigger servers

### What the Support Agents Experience

**Without thread pool:**
- Latency gradually increases as load builds
- At 200 concurrent users, some requests timeout
- Audio processing backs up, transcripts lag behind speech

**With thread pool:**
- Event loop stays responsive
- VAD work parallelized across CPU cores
- Consistent latency even at high concurrency
- p99 latency stays under 100ms target

### The Async Audit

Before Phase 3, we audit all code for blocking calls:

```python
# BLOCKING - breaks high concurrency
time.sleep(1)                    # Use: await asyncio.sleep(1)
requests.get(url)                # Use: await httpx.get(url)
with open(f) as f: f.read()      # Use: async with aiofiles.open(f)

# CPU-BOUND - move to thread pool
vad.is_speech(audio)             # Use: run_in_executor()
json.loads(huge_string)          # Use: run_in_executor() if huge
```

The goal: the event loop should never block for more than ~1ms.

---

## 5. Operator Tuning for Different Use Cases

### The Situation

Two customers use the same transcription service:

**Customer A: Real-time captions for webinars**
- Many concurrent viewers (1000+)
- Short utterances acceptable
- Latency is critical (people watch the delay)
- Session duration: 1-2 hours

**Customer B: Medical dictation**
- Few concurrent users (50)
- Complete sentences important
- Accuracy over latency
- Session duration: minutes

They have different needs, but should they need different code?

### The Problem

Hard-coded values force one-size-fits-all:
- 300ms endpointing is too aggressive for medical dictation
- 1000 session limit is overkill for 50-user deployment
- Defaults optimized for one case hurt the other

### How the Code Handles It

**Environment-driven configuration:**

```python
class Settings(BaseSettings):
    # Timing
    endpointing_ms: int = 300
    idle_timeout_seconds: int = 300

    # Capacity
    max_sessions_per_worker: int = 500
    max_global_sessions: int = 2000

    # Performance
    vad_thread_pool_size: int = 4
    max_send_queue_size: int = 100

    class Config:
        env_prefix = "ASR_"
        env_file = ".env"
```

**Customer A deployment:**
```bash
# webinar-captions.env
ASR_ENDPOINTING_MS=200          # Fast finalization
ASR_MAX_SESSIONS_PER_WORKER=1000  # High capacity
ASR_MAX_GLOBAL_SESSIONS=5000
ASR_IDLE_TIMEOUT_SECONDS=7200   # 2 hours for long events
ASR_VAD_THREAD_POOL_SIZE=8      # Big server
```

**Customer B deployment:**
```bash
# medical-dictation.env
ASR_ENDPOINTING_MS=800          # Wait for complete sentences
ASR_MAX_SESSIONS_PER_WORKER=100 # Don't need many
ASR_MAX_GLOBAL_SESSIONS=200
ASR_IDLE_TIMEOUT_SECONDS=600    # 10 min timeout
ASR_VAD_THREAD_POOL_SIZE=2      # Smaller server
```

### Configuration Validation

Bad configuration should fail fast, not cause mysterious runtime issues:

```python
@field_validator("endpointing_ms")
@classmethod
def validate_endpointing(cls, v):
    if v < 100:
        raise ValueError(
            "endpointing_ms < 100 will cause excessive fragmentation. "
            "Users will see many tiny utterances. Minimum recommended: 200"
        )
    if v > 5000:
        raise ValueError(
            "endpointing_ms > 5000 will cause poor UX. "
            "Users will wait 5+ seconds after speaking to see finals."
        )
    return v
```

### Startup Logging

Operators can verify configuration is correct:

```python
@app.on_event("startup")
async def log_config():
    settings = get_settings()
    logger.info("Starting with configuration:")
    logger.info(f"  endpointing_ms: {settings.endpointing_ms}")
    logger.info(f"  max_sessions_per_worker: {settings.max_sessions_per_worker}")
    logger.info(f"  redis_url: {'****' if settings.redis_url else 'None (in-memory)'}")
    # ...
```

Output:
```
INFO: Starting with configuration:
INFO:   endpointing_ms: 800
INFO:   max_sessions_per_worker: 100
INFO:   redis_url: None (in-memory)
```

### Why This Matters

**Without configuration:**
- Code changes required for each deployment
- Testing nightmare (is this the "fast" version or "accurate" version?)
- Can't A/B test different settings

**With configuration:**
- Same code, different behavior
- Easy to experiment
- CI/CD can deploy same artifact everywhere
- Settings documented by their existence

---

## 6. Worker Crashes Mid-Session

### The Situation

Emma is halfway through transcribing a long interview. Worker 3, which is handling her session, crashes due to an OOM error from a different process on the same machine.

Her WebSocket connection dies. Her client reconnects, but hits Worker 1 this time (load balancer doesn't know about sticky sessions for new connections).

### The Problem

Without externalized state:
- Worker 3 had Emma's session in memory
- Worker 3 died, memory is gone
- Worker 1 has no idea Emma had a session
- Emma has to start over
- Her metrics are lost

With externalized state but poor design:
- Session state is in Redis
- But Worker 1 can't "take over" a session owned by Worker 3
- Session is orphaned until it times out
- Emma still can't continue

### How the Code Handles It

**Session state in Redis persists:**

```python
state = SessionState(
    session_id="abc-123",
    state="active",
    worker_id="worker-3",  # Original owner
    created_at=...,
    last_activity_at=...,
    metrics={
        "audio_bytes_received": 1_500_000,
        "transcripts_sent": 42,
    }
)
# This survives Worker 3's crash
await self._redis.set("session:abc-123", state.to_json())
```

**Orphan detection:**

Background cleanup on all workers:

```python
async def _cleanup_orphaned_sessions(self):
    """Clean up sessions from dead workers."""
    async for key in self._redis.scan_iter("session:*"):
        state = await self._get_state(key)

        if state.worker_id not in active_workers:
            # Owner is gone - session is orphaned
            if now - state.last_activity_at > orphan_timeout:
                # No activity since owner died - clean up
                await self._redis.delete(key)
                logger.info(f"Cleaned orphaned session {state.session_id}")
```

**Client reconnection:**

Emma's client app handles the disconnect:

```javascript
// Client-side
websocket.onclose = async () => {
    // Try to reconnect
    await sleep(1000);
    const newWs = new WebSocket(TRANSCRIPTION_URL);

    // Tell server about previous session (optional)
    newWs.send(JSON.stringify({
        type: "reconnect",
        previous_session_id: lastSessionId
    }));
};
```

Server handling:

```python
async def handle_reconnect(websocket, previous_session_id):
    # Check if previous session still exists
    state = await session_store.get(previous_session_id)

    if state and state.state == "active":
        # Session exists but owned by dead worker
        # Option 1: Take it over (complex)
        # Option 2: Return the metrics, start fresh

        await websocket.send_json({
            "type": "session_recovered",
            "previous_metrics": state.metrics,
            "message": "Previous session recovered. Starting new session."
        })

    # Create new session
    session = await session_manager.create_session()
    # ...
```

### What Emma Experiences

1. Transcribing interview, everything working
2. Connection drops (Worker 3 crashed)
3. Client shows "Connection lost, reconnecting..."
4. Reconnect succeeds (hits Worker 1)
5. Server reports: "Previous session recovered. 42 transcripts completed."
6. Emma continues transcribing (new session, but knows where she was)

### The Tradeoff

Full session takeover (Worker 1 continuing exactly where Worker 3 left off) is complex:
- Need to transfer in-memory state (VAD buffer, silence counter)
- Need to synchronize with client's expectations
- Edge cases around in-flight messages

The simpler approach:
- Preserve metrics and metadata
- Start fresh transcription processing
- Client handles the gap (usually just a second or two)

For real-time transcription, this is usually acceptable.

---

## 7. Gradual Traffic Increase (Scaling Decision)

### The Situation

The transcription service starts with 100 users. Over 6 months, it grows:
- Month 1: 100 users
- Month 3: 500 users
- Month 6: 2,000 users

The ops team needs to know when to add capacity.

### The Problem

Without metrics visibility:
- "Is the server slow?" / "I don't know"
- "Should we add another worker?" / "Maybe?"
- "Why did it crash last night?" / "No idea"

Scaling decisions become guesswork.

### How the Code Handles It

**Metrics endpoint:**

```python
@router.get("/v1/metrics")
async def get_metrics():
    return {
        "active_sessions": session_manager.get_active_count(),
        "max_sessions": settings.max_global_sessions,
        "capacity_percent": (active / max) * 100,

        "sessions_by_state": {
            "created": count_by_state("created"),
            "active": count_by_state("active"),
            "closing": count_by_state("closing"),
        },

        "worker_id": worker_info.worker_id,
        "uptime_seconds": (now - startup_time).total_seconds(),
    }
```

**Prometheus integration (optional):**

```python
from prometheus_client import Counter, Gauge, Histogram

sessions_total = Counter(
    "asr_sessions_total",
    "Total sessions created"
)

active_sessions = Gauge(
    "asr_active_sessions",
    "Currently active sessions"
)

request_latency = Histogram(
    "asr_request_latency_seconds",
    "Request latency",
    buckets=[.01, .025, .05, .1, .25, .5, 1.0]
)
```

### What Ops Sees

**Grafana dashboard:**
- Active sessions over time (steady increase = growth)
- Capacity percentage (alert when > 80%)
- p99 latency (alert when > 100ms)
- Error rate (alert when > 1%)

**Alert triggers:**
- "Capacity at 85% for 10 minutes" → Add worker
- "p99 latency > 200ms" → Check thread pool size
- "Error rate > 5%" → Investigate immediately

### The Scaling Decision

Month 3, capacity hits 80%:

```
Current: 1 worker × 500 max sessions = 500 capacity
Usage: 400 sessions (80%)
Growth rate: ~50 sessions/month

Action: Add second worker
New capacity: 2 workers × 500 = 1000 sessions
Headroom: Good for 6+ months
```

Configuration change:
```yaml
# kubernetes deployment
replicas: 2  # was 1
```

That's it. Workers share Redis state, capacity doubles.

---

## Summary

These scenarios drive Phase 3's design:

| Scenario | Key Mechanism |
|----------|---------------|
| Flaky mobile connection | Send queue with backpressure |
| Malicious large chunks | Per-chunk size limits |
| High-traffic events | Externalized state + global limits |
| CPU-bound VAD | Thread pool for blocking work |
| Different customer needs | Environment-driven configuration |
| Worker crashes | Redis state persistence |
| Gradual growth | Metrics endpoint + observability |

Each mechanism exists because of a real operational need. The goal is a system that:
- **Stays responsive** under adverse conditions
- **Scales horizontally** with minimal friction
- **Fails gracefully** when limits are hit
- **Is observable** so operators can make informed decisions

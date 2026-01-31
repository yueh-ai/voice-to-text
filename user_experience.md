# User Experience Scenarios

This document explains the user scenarios that drive Phase 2's session management design. Each scenario describes a real situation, the problem it creates, and how the code handles it.

---

## 1. User Connects But Doesn't Speak Immediately

### The Situation

Maria opens the transcription app on her phone before a client call. She wants it ready to go, but the call hasn't started yet. She's staring at the "Listening..." indicator, waiting for her client to pick up.

Three minutes pass. The client finally answers, and Maria starts speaking.

### The Problem

From the server's perspective, Maria's WebSocket connection has been open for 3 minutes with zero audio. Is this:
- A legitimate user waiting to speak?
- An abandoned tab the user forgot about?
- A bot probing our endpoints?

We can't be aggressive with timeouts - kicking Maria off right before her call starts would be a terrible experience. But we also can't hold connections forever, or a few thousand abandoned tabs could exhaust server resources.

### How the Code Handles It

The session starts in `CREATED` state, not `ACTIVE`. This distinction matters:

```python
class SessionState(Enum):
    CREATED = "created"   # Connected, but no audio yet
    ACTIVE = "active"     # Has received audio, actively transcribing
    CLOSING = "closing"
    CLOSED = "closed"
```

When Maria connects, her session is `CREATED`. The idle timeout (default 5 minutes) applies, but it's generous enough for her use case:

```python
session = TranscriptionSession(models, config)
# session.state == SessionState.CREATED
# session.last_activity_at == now
```

When she finally speaks, the first audio chunk transitions her to `ACTIVE`:

```python
async def process_chunk(self, audio: bytes) -> TranscriptResult:
    if self.state == SessionState.CREATED:
        self.state = SessionState.ACTIVE  # Now actively transcribing

    self.last_activity_at = datetime.utcnow()  # Reset idle timer
    # ... process audio ...
```

If Maria had truly abandoned the connection (no audio for 5+ minutes), the background cleanup task would close her session:

```python
async def _cleanup_idle_sessions(self):
    now = datetime.utcnow()
    timeout = timedelta(seconds=self.manager_config.idle_timeout_seconds)

    for session_id, session in self._sessions.items():
        if now - session.last_activity_at > timeout:
            logger.info(f"Session {session_id} idle timeout")
            await self.close_session(session_id)
```

### The Balance

- **User-friendly**: 5 minutes is long enough for most "waiting to start" scenarios
- **Resource-safe**: Abandoned connections don't accumulate forever
- **Observable**: We log idle timeouts so ops can tune the threshold if needed

---

## 2. User Finishes Speaking (End of Sentence)

### The Situation

David is dictating an email: "Please send the quarterly report to the marketing team."

He stops talking. He's done with that sentence and waiting for the transcription to appear so he can dictate the next one.

### The Problem

How does the server know David is done? He could be:
- Finished with that thought (wants to see the final transcription)
- Pausing to think of the next word (more audio coming soon)
- Taking a breath mid-sentence

If we finalize too quickly, we might cut off "...to the marketing team" as a separate fragment. If we wait too long, David is staring at a partial transcription wondering if the system is frozen.

### How the Code Handles It

The Voice Activity Detection (VAD) continuously monitors audio chunks. When it detects silence, it starts counting:

```python
async def process_chunk(self, audio: bytes) -> TranscriptResult:
    chunk_duration_ms = self._chunk_duration_ms(audio)

    if self.vad_session.is_speech(audio):
        # David is talking - reset silence counter
        self._silence_duration_ms = 0
        text = self.models.asr.transcribe_sync(audio)
        return TranscriptResult(text=text, is_final=False, ...)
    else:
        # Silence detected - how long has it been?
        self._silence_duration_ms += chunk_duration_ms

        if self._silence_duration_ms >= self.config.endpointing_ms:
            # Enough silence - finalize this utterance
            self._reset()
            return TranscriptResult(text="", is_final=True, ...)

        # Not enough silence yet - keep waiting
        return TranscriptResult(text="", is_final=False, ...)
```

The `endpointing_ms` threshold (default 300ms) is the key tuning parameter:
- **Too short (100ms)**: Normal speech pauses trigger false finals
- **Too long (1000ms)**: Users wait awkwardly after finishing
- **300ms**: Matches natural speech rhythm for most users

### What David Sees

1. He says "Please send the quarterly report..."
2. Partial transcriptions appear as he speaks: "Please" → "Please send" → "Please send the quarterly"
3. He stops talking
4. ~300ms of silence passes
5. Server sends `{"type": "final"}`
6. Client knows this utterance is complete and can display it as final
7. David's session stays `ACTIVE` - he can start the next sentence immediately

### The Nuance

The `is_final=True` doesn't close the session - it just marks the end of one utterance. David can immediately continue: "Also, schedule a follow-up meeting for next Tuesday."

Each sentence becomes its own speech → silence → final cycle within the same session.

---

## 3. User Disconnects Abruptly

### The Situation

Priya is transcribing meeting notes on her laptop. Halfway through, her laptop lid closes (sleep mode), or she loses WiFi, or her browser crashes.

The server never receives a clean "stop" message. The TCP connection just... dies.

### The Problem

Without explicit cleanup:
- The `TranscriptionSession` object stays in memory
- The session count never decreases
- After enough disconnects, we hit max sessions and reject new users
- Memory usage grows unbounded

This is a resource leak, and it's especially dangerous because it's invisible - everything looks fine until suddenly the server is overloaded.

### How the Code Handles It

Python's `try/finally` guarantees cleanup runs regardless of how the connection ends:

```python
@router.websocket("/v1/transcribe/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    session = None

    try:
        session = await session_manager.create_session()

        while True:
            data = await websocket.receive_text()  # Blocks here
            # ... process messages ...

    except WebSocketDisconnect:
        # Starlette raises this when connection drops
        pass
    finally:
        # THIS ALWAYS RUNS - clean disconnect, crash, timeout, anything
        if session:
            await session_manager.close_session(session.session_id)
```

When Priya's laptop sleeps:
1. The TCP connection goes stale
2. The next `receive_text()` fails (connection closed or timeout)
3. Starlette raises `WebSocketDisconnect`
4. The `except` block catches it (we don't need to do anything special)
5. The `finally` block runs, closing her session properly

### The Session Close Process

```python
async def close_session(self, session_id: str):
    async with self._lock:
        session = self._sessions.get(session_id)
        if session:
            await session.close()  # Cleanup internal state
            del self._sessions[session_id]  # Remove from registry
            logger.debug(f"Closed session {session_id}")
```

The session's `close()` method handles internal cleanup:

```python
async def close(self):
    if self.state in (SessionState.CLOSING, SessionState.CLOSED):
        return  # Already closing/closed, don't double-cleanup

    self.state = SessionState.CLOSING

    # Release resources
    self.vad_session.reset()  # Clear VAD buffers
    self._reset()             # Clear speech buffers

    self.state = SessionState.CLOSED
```

### Defense in Depth

Even if the `finally` block somehow doesn't run (server crash, OOM kill), the background cleanup task provides a second layer of protection:

```python
async def _cleanup_idle_sessions(self):
    # Runs every 30 seconds
    for session in self._sessions.values():
        if session.state == SessionState.CLOSED:
            # Already closed but not removed - clean it up
            to_close.append(session_id)
        elif now - session.last_activity_at > timeout:
            # Idle too long - probably orphaned
            to_close.append(session_id)
```

---

## 4. User Pauses Mid-Sentence (Thinking)

### The Situation

James is dictating a complex thought: "The Q3 revenue was..."

He pauses for half a second, trying to remember the exact number.

"...fourteen point seven million, which exceeded projections."

### The Problem

James paused for 500ms - longer than our 300ms endpointing threshold. If we're not careful, we'd split this into two separate utterances:
- "The Q3 revenue was"
- "fourteen point seven million which exceeded projections"

This would be confusing and would lose the sentence structure.

### Wait, Is This Actually a Problem?

Let's think about this more carefully. Our current design with `endpointing_ms = 300ms` would indeed trigger a `final` after James's pause. But is that wrong?

**Arguments for splitting:**
- Client can show "The Q3 revenue was" as complete, reducing perceived latency
- If James never continues (phone rings, he gets distracted), we've captured what he said
- The client can concatenate finals if it wants continuous text

**Arguments against splitting:**
- Loses the semantic connection between phrases
- More finals = more network messages
- Some use cases (live captions) want natural sentence boundaries

### How Different Configurations Handle It

**Option A: Short endpointing (300ms)** - Current default
```python
endpointing_ms = 300
```
- James's pause triggers a final
- Client receives two separate utterances
- Fast feedback, but fragments long thoughts

**Option B: Long endpointing (800ms)**
```python
endpointing_ms = 800
```
- James's 500ms pause doesn't trigger a final
- "The Q3 revenue was fourteen point seven million..." stays together
- But all users wait 800ms after speaking to see finals

**Option C: Client-side concatenation**
- Keep short endpointing for fast feedback
- Client concatenates recent finals within a time window
- Best of both worlds, but more client complexity

### The Design Decision

We chose **Option A (short endpointing)** as the default because:

1. **Partial results provide context**: The client sees "The Q3 revenue was" before the final, so the split isn't jarring
2. **Configurable**: Users who need longer phrases can increase `ASR_ENDPOINTING_MS`
3. **Client flexibility**: The client can concatenate if needed - we can't un-split on the server

```python
# Users can tune this via environment variable
class Settings(BaseSettings):
    endpointing_ms: int = 300  # Tune higher for fewer splits

    class Config:
        env_prefix = "ASR_"
```

### What James Actually Experiences

1. "The Q3 revenue was..." → partials stream to client
2. 300ms silence → `{"type": "final"}`
3. "fourteen point seven million..." → new partials stream
4. Another pause → another `{"type": "final"}`

The session stays `ACTIVE` throughout. James can keep dictating as long as he wants.

---

## 5. Server Hits Max Session Limit

### The Situation

It's the company all-hands meeting. 1,200 employees try to connect to the live transcription service simultaneously. The server is configured for `max_sessions = 1000`.

Employee #1001 clicks "Start Transcription" and... what happens?

### The Problem

Without limits:
- Memory usage grows linearly with connections
- CPU contention degrades transcription quality for everyone
- Eventually the server OOMs or becomes unresponsive
- Everyone has a bad experience

With limits but poor UX:
- User sees a generic "Connection failed" error
- They retry, making the problem worse
- Support tickets pile up

### How the Code Handles It

The session manager enforces the limit at creation time:

```python
async def create_session(self) -> TranscriptionSession:
    async with self._lock:
        # Count only non-closing sessions
        active_count = sum(
            1 for s in self._sessions.values()
            if s.state not in (SessionState.CLOSING, SessionState.CLOSED)
        )

        if active_count >= self.manager_config.max_sessions:
            raise SessionLimitExceeded(
                f"Maximum {self.manager_config.max_sessions} concurrent sessions reached"
            )

        # Under limit - create the session
        session = TranscriptionSession(self.models, self.config)
        self._sessions[session.session_id] = session
        return session
```

The WebSocket handler catches this and sends a clear error:

```python
@router.websocket("/v1/transcribe/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()

    try:
        session = await session_manager.create_session()
    except SessionLimitExceeded as e:
        await websocket.send_json({
            "type": "error",
            "message": str(e),
            "code": "SESSION_LIMIT",
        })
        await websocket.close(code=1008)  # Policy Violation
        return

    # ... continue with session ...
```

### What Employee #1001 Sees

1. They click "Start Transcription"
2. WebSocket connects (TCP level succeeds)
3. Server immediately sends:
   ```json
   {
     "type": "error",
     "message": "Maximum 1000 concurrent sessions reached",
     "code": "SESSION_LIMIT"
   }
   ```
4. Connection closes with code 1008
5. Client can show: "Service is at capacity. Please try again in a few minutes."

### Why This UX Matters

**Clear error code**: `SESSION_LIMIT` tells the client exactly what's wrong. The client can:
- Show a specific message ("Service busy" not "Connection failed")
- Implement exponential backoff retry
- Show a queue position if we add that later

**Immediate rejection**: We don't accept the connection, start processing, then fail. The rejection happens before any resources are allocated for this session.

**WebSocket close code 1008**: This is the standard code for "Policy Violation" - the client did nothing wrong technically, but the server is enforcing a policy limit.

### The Operations View

The metrics endpoint shows capacity:

```python
@router.get("/v1/sessions/metrics")
async def get_metrics(session_manager = Depends(get_session_manager)):
    return {
        "active_sessions": session_manager.get_active_count(),
        "max_sessions": session_manager.manager_config.max_sessions,
        "capacity_percent": (active / max) * 100,
        # ...
    }
```

Ops can:
- Alert when capacity > 80%
- Scale up workers (Phase 3)
- Increase `ASR_MAX_SESSIONS` if server has headroom

---

## 6. Long-Running Session (Hours of Transcription)

### The Situation

Dr. Chen is using the transcription service for a 3-hour surgery, narrating her observations throughout. The session has been running continuously, with audio streaming the entire time.

### The Problem

Long-running sessions can accumulate issues:
- Memory leaks (small allocations that never get freed)
- Metrics overflow (counters hitting max int)
- Stale state (edge cases in state machines)
- Resource exhaustion (file handles, buffer growth)

A session that works fine for 5 minutes might fail at hour 2.

### How the Code Handles It

**Bounded buffers**: The VAD session doesn't accumulate audio forever:

```python
class VADSession:
    def __init__(self, model: VADModel, frame_duration_ms: int = 20):
        self._buffer = bytearray()  # Accumulates until we have a full frame
        self._frame_size = ...      # Fixed size based on frame_duration_ms

    def is_speech(self, audio: bytes) -> bool:
        self._buffer.extend(audio)

        # Process complete frames, keep only remainder
        while len(self._buffer) >= self._frame_size:
            frame = bytes(self._buffer[:self._frame_size])
            self._buffer = self._buffer[self._frame_size:]  # Discard processed
            # ... check frame ...

        # Buffer never grows beyond frame_size - 1
        return result
```

**Stateless transcription**: The ASR model doesn't accumulate state:

```python
class MockASRModel:
    def transcribe_sync(self, audio: bytes) -> str:
        # Each call is independent - no accumulated state
        return self.text_gen.generate(len(audio))
```

**Reset on silence**: After each utterance, state is cleared:

```python
def _reset(self):
    self._speech_buffer_bytes = 0
    self._silence_duration_ms = 0.0
    self.vad_session.reset()  # Clears VAD buffer too
```

**Metrics use appropriate types**:

```python
@dataclass
class SessionMetrics:
    audio_bytes_received: int = 0  # Python int has arbitrary precision
    # 3 hours of 16kHz 16-bit audio = ~346 MB
    # Well within int range, and Python ints don't overflow anyway
```

### What Dr. Chen Experiences

Hour 0: Session starts, everything works.
Hour 1: Still working. Metrics show `audio_bytes_received: 115_200_000`.
Hour 2: Still working. VAD has processed thousands of frames but buffer is still tiny.
Hour 3: Surgery ends. Dr. Chen sends "stop", session closes cleanly.

### The Idle Timeout Question

But wait - what about idle timeout? Dr. Chen might have 10-minute periods of silence during the surgery.

The key is `last_activity_at` updates on every audio chunk, not just speech:

```python
async def process_chunk(self, audio: bytes) -> TranscriptResult:
    self.last_activity_at = datetime.utcnow()  # Even for silence
    # ...
```

As long as the client keeps sending audio (even silence), the session stays alive. The idle timeout only triggers when the client stops sending entirely.

---

## 7. Burst of New Connections (100 Users Join a Webinar)

### The Situation

A popular tech streamer announces "I'm turning on live captions - click the link to see transcription!"

Within 3 seconds, 100 viewers click the link and connect to the transcription service.

### The Problem

Bursts are dangerous because:
- Connection storms can overwhelm the event loop
- Lock contention on the session registry
- Memory allocation spikes
- If we're near capacity, lots of rejections happen simultaneously

### How the Code Handles It

**Async lock prevents race conditions**:

```python
async def create_session(self) -> TranscriptionSession:
    async with self._lock:  # Only one creation at a time
        active_count = sum(...)

        if active_count >= self.manager_config.max_sessions:
            raise SessionLimitExceeded(...)

        session = TranscriptionSession(self.models, self.config)
        self._sessions[session.session_id] = session
        return session
```

The `asyncio.Lock` ensures:
- Session count is always accurate (no race between check and create)
- Sessions are added atomically to the registry
- No duplicate session IDs

**But doesn't the lock serialize everything?**

Yes, session creation is serialized. But creation is fast (~microseconds):
- Allocate `TranscriptionSession` object
- Add to dict
- That's it

The lock doesn't affect:
- Audio processing (happens outside the lock)
- WebSocket message handling
- Other sessions' work

100 sessions created serially in microseconds each = ~1ms total. Imperceptible.

**Lightweight session objects**:

```python
class TranscriptionSession:
    def __init__(self, models: Models, config: Settings):
        self.session_id = str(uuid.uuid4())  # Just a string
        self.state = SessionState.CREATED     # Just an enum
        self.created_at = datetime.utcnow()   # Just a timestamp

        # VADSession is also lightweight - no model loading
        self.vad_session = VADSession(model=models.vad, ...)

        # Just integers
        self._speech_buffer_bytes = 0
        self._silence_duration_ms = 0.0
```

No heavy initialization. Models are already loaded (shared singleton). Each session is ~1KB of memory.

### What the 100 Viewers Experience

1. All 100 connections are accepted by the OS (TCP backlog)
2. Starlette's event loop accepts them one by one (fast)
3. Each handler calls `create_session()`:
   - Connection 1-100: All succeed (under 1000 limit)
   - Each session created in microseconds
4. All 100 are transcribing within ~100ms of the burst
5. Audio processing happens concurrently (async)

### If We Were Near Capacity

Say we had 950 sessions and 100 new connections arrive:

- Connections 1-50: Created successfully (950 → 1000)
- Connections 51-100: Get `SESSION_LIMIT` error immediately
- No partial work wasted
- Clear error message for rejected users

### Monitoring the Burst

```python
logger.debug(f"Created session {session.session_id}, active: {active_count + 1}")
```

The logs would show:
```
Created session abc123, active: 951
Created session def456, active: 952
...
Created session xyz789, active: 1000
Session limit exceeded: Maximum 1000 concurrent sessions reached
Session limit exceeded: Maximum 1000 concurrent sessions reached
...
```

Ops can see exactly when capacity was hit and how many were rejected.

---

## Summary

These scenarios drive the Phase 2 design:

| Scenario | Key Mechanism |
|----------|---------------|
| Delayed start | `CREATED` vs `ACTIVE` state distinction |
| End of sentence | `endpointing_ms` silence threshold |
| Abrupt disconnect | `finally` block + background cleanup |
| Mid-sentence pause | Configurable threshold, client concatenation |
| Capacity limit | Atomic check + clear error codes |
| Long sessions | Bounded buffers, stateless processing |
| Connection bursts | Fast creation, async lock, lightweight objects |

Each mechanism exists because of a real user need, not just technical elegance.

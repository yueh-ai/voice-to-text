# Phase 2 — Session Management

Detailed implementation plan for Phase 2 of the Scalable Transcription Service.

## Overview

Phase 1.5 established the shared model architecture with lightweight `TranscriptionSession` per user. Phase 2 adds proper **session lifecycle management** to handle many concurrent users cleanly.

**Current State:**
- `TranscriptionSession` exists but is created/destroyed ad-hoc per WebSocket connection
- No centralized registry of active sessions
- No lifecycle state machine
- No limits on concurrent connections
- No metrics or visibility into session behavior

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Session ID generation | UUID4 | Simple, collision-free, no coordination needed |
| Session storage | In-memory dict | Good for single-instance; swap for Redis in Phase 3 |
| Lifecycle states | 4-state machine | Clean transitions, easy to reason about |
| Timeout handling | Background task | Non-blocking, periodic cleanup |
| Connection limits | Per-instance | Simple; load balancer handles distribution |
| Metrics storage | In-session + aggregated | Per-session detail, efficient querying |

---

## Dependencies

No new dependencies required. Uses existing:
- `asyncio` for background tasks and locks
- `uuid` for session ID generation
- `time` for timestamps

---

## Project Structure Changes

```
src/transcription_service/
├── core/
│   ├── session.py           # Updated: add lifecycle state
│   ├── session_manager.py   # NEW: centralized registry
│   └── ...
├── api/
│   ├── stream.py            # Updated: use session manager
│   ├── transcribe.py        # Updated: use session manager
│   ├── sessions.py          # NEW: session inspection endpoints
│   └── ...
└── ...

tests/
├── test_session_manager.py  # NEW: session manager tests
├── test_session_lifecycle.py # NEW: lifecycle tests
├── test_concurrent.py       # NEW: concurrency tests
└── ...
```

---

## Component Specifications

### 1. Session Lifecycle States

```
    ┌─────────┐
    │ CREATED │  Session object exists, not yet active
    └────┬────┘
         │ first audio received
         ▼
    ┌─────────┐
    │ ACTIVE  │  Processing audio, receiving/sending messages
    └────┬────┘
         │ stop command OR timeout OR disconnect
         ▼
    ┌─────────┐
    │ CLOSING │  Cleanup in progress, no new audio accepted
    └────┬────┘
         │ cleanup complete
         ▼
    ┌─────────┐
    │ CLOSED  │  Terminal state, ready for removal
    └─────────┘
```

**State Transitions:**
- `CREATED → ACTIVE`: First audio chunk received
- `ACTIVE → CLOSING`: Stop command, client disconnect, or idle timeout
- `CLOSING → CLOSED`: Cleanup tasks complete
- Any state → `CLOSING`: Error or forced termination

### 2. Session Model Updates (`core/session.py`)

```python
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

class SessionState(Enum):
    CREATED = "created"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"

@dataclass
class SessionMetrics:
    """Per-session metrics."""
    audio_bytes_received: int = 0
    audio_chunks_received: int = 0
    transcripts_sent: int = 0
    partials_sent: int = 0
    finals_sent: int = 0
    errors_sent: int = 0

    @property
    def audio_duration_ms(self) -> float:
        """Estimated audio duration based on bytes (16kHz, 16-bit)."""
        return self.audio_bytes_received / 32.0  # 32 bytes per ms

@dataclass
class SessionInfo:
    """Session metadata for inspection."""
    session_id: str
    state: SessionState
    created_at: datetime
    last_activity_at: datetime
    metrics: SessionMetrics

class TranscriptionSession:
    """Updated with lifecycle state and metrics."""

    def __init__(self, models: Models, config: Settings):
        self.session_id: str = str(uuid.uuid4())
        self.state: SessionState = SessionState.CREATED
        self.created_at: datetime = datetime.utcnow()
        self.last_activity_at: datetime = self.created_at
        self.metrics: SessionMetrics = SessionMetrics()

        # ... existing initialization ...

    async def process_chunk(self, audio: bytes) -> TranscriptResult:
        """Updated to track state and metrics."""
        if self.state == SessionState.CLOSING:
            raise SessionClosingError("Session is closing, cannot accept audio")

        if self.state == SessionState.CREATED:
            self.state = SessionState.ACTIVE

        self.last_activity_at = datetime.utcnow()
        self.metrics.audio_bytes_received += len(audio)
        self.metrics.audio_chunks_received += 1

        result = await self._process_chunk_internal(audio)

        if result.text:
            self.metrics.partials_sent += 1
        if result.is_final:
            self.metrics.finals_sent += 1
        self.metrics.transcripts_sent += 1

        return result

    async def close(self):
        """Initiate graceful shutdown."""
        if self.state in (SessionState.CLOSING, SessionState.CLOSED):
            return

        self.state = SessionState.CLOSING
        # Cleanup resources (VAD session, buffers)
        self.vad_session.reset()
        self._reset()
        self.state = SessionState.CLOSED

    def get_info(self) -> SessionInfo:
        """Return session metadata for inspection."""
        return SessionInfo(
            session_id=self.session_id,
            state=self.state,
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            metrics=self.metrics,
        )
```

### 3. Session Manager (`core/session_manager.py`)

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

@dataclass
class SessionManagerConfig:
    """Configuration for session manager."""
    max_sessions: int = 1000
    idle_timeout_seconds: float = 300.0  # 5 minutes
    cleanup_interval_seconds: float = 30.0

class SessionLimitExceeded(Exception):
    """Raised when max concurrent sessions reached."""
    pass

class SessionNotFound(Exception):
    """Raised when session ID not found."""
    pass

class SessionManager:
    """
    Centralized session registry with lifecycle management.

    Responsibilities:
    - Create and track sessions
    - Enforce concurrent session limits
    - Clean up idle/disconnected sessions
    - Provide session inspection
    """

    def __init__(self, models: Models, config: Settings, manager_config: SessionManagerConfig = None):
        self.models = models
        self.config = config
        self.manager_config = manager_config or SessionManagerConfig()

        self._sessions: Dict[str, TranscriptionSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Session manager started")

    async def stop(self):
        """Stop cleanup task and close all sessions."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all active sessions
        async with self._lock:
            for session in self._sessions.values():
                await session.close()
            self._sessions.clear()

        logger.info("Session manager stopped")

    async def create_session(self) -> TranscriptionSession:
        """
        Create a new session.

        Raises:
            SessionLimitExceeded: If max sessions reached
        """
        async with self._lock:
            # Check limit
            active_count = sum(
                1 for s in self._sessions.values()
                if s.state not in (SessionState.CLOSING, SessionState.CLOSED)
            )

            if active_count >= self.manager_config.max_sessions:
                raise SessionLimitExceeded(
                    f"Maximum {self.manager_config.max_sessions} concurrent sessions reached"
                )

            session = TranscriptionSession(self.models, self.config)
            self._sessions[session.session_id] = session

            logger.debug(f"Created session {session.session_id}, active: {active_count + 1}")
            return session

    async def get_session(self, session_id: str) -> TranscriptionSession:
        """Get session by ID."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise SessionNotFound(f"Session {session_id} not found")
            return session

    async def close_session(self, session_id: str):
        """Close and remove a session."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                await session.close()
                del self._sessions[session_id]
                logger.debug(f"Closed session {session_id}")

    async def _cleanup_loop(self):
        """Background task to clean up idle sessions."""
        while True:
            await asyncio.sleep(self.manager_config.cleanup_interval_seconds)
            await self._cleanup_idle_sessions()

    async def _cleanup_idle_sessions(self):
        """Remove sessions that have been idle too long."""
        now = datetime.utcnow()
        timeout = timedelta(seconds=self.manager_config.idle_timeout_seconds)

        to_close = []

        async with self._lock:
            for session_id, session in self._sessions.items():
                if session.state == SessionState.CLOSED:
                    to_close.append(session_id)
                elif now - session.last_activity_at > timeout:
                    logger.info(f"Session {session_id} idle timeout")
                    to_close.append(session_id)

        for session_id in to_close:
            await self.close_session(session_id)

        if to_close:
            logger.info(f"Cleaned up {len(to_close)} idle sessions")

    # Inspection methods

    def get_active_count(self) -> int:
        """Get count of active sessions (non-blocking snapshot)."""
        return sum(
            1 for s in self._sessions.values()
            if s.state in (SessionState.CREATED, SessionState.ACTIVE)
        )

    def get_all_sessions(self) -> list[SessionInfo]:
        """Get info for all sessions."""
        return [s.get_info() for s in self._sessions.values()]

    def get_aggregate_metrics(self) -> dict:
        """Get aggregated metrics across all sessions."""
        total_audio_bytes = 0
        total_chunks = 0
        total_transcripts = 0

        for session in self._sessions.values():
            m = session.metrics
            total_audio_bytes += m.audio_bytes_received
            total_chunks += m.audio_chunks_received
            total_transcripts += m.transcripts_sent

        return {
            "active_sessions": self.get_active_count(),
            "total_sessions": len(self._sessions),
            "total_audio_bytes": total_audio_bytes,
            "total_audio_duration_ms": total_audio_bytes / 32.0,
            "total_chunks": total_chunks,
            "total_transcripts": total_transcripts,
        }
```

### 4. App Integration (`main.py`)

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    config = get_settings()
    init_models(config)

    # Initialize session manager
    models = get_models()
    session_manager = SessionManager(models, config)
    await session_manager.start()
    app.state.session_manager = session_manager

    yield

    # Shutdown
    await session_manager.stop()

def get_session_manager() -> SessionManager:
    """FastAPI dependency for session manager."""
    from transcription_service.main import app
    return app.state.session_manager
```

### 5. Updated Stream Endpoint (`api/stream.py`)

```python
@router.websocket("/v1/transcribe/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()

    session_manager = get_session_manager()
    session = None

    try:
        # Create session (may raise SessionLimitExceeded)
        try:
            session = await session_manager.create_session()
        except SessionLimitExceeded as e:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
                "code": "SESSION_LIMIT",
            })
            await websocket.close(code=1008)  # Policy violation
            return

        # Send session ID to client
        await websocket.send_json({
            "type": "session_start",
            "session_id": session.session_id,
        })

        while True:
            # ... existing message handling ...
            # Use session.process_chunk() as before
            pass

    except WebSocketDisconnect:
        pass
    finally:
        # Always clean up
        if session:
            await session_manager.close_session(session.session_id)
```

### 6. Session Inspection Endpoints (`api/sessions.py`)

```python
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

@router.get("")
async def list_sessions(
    session_manager: SessionManager = Depends(get_session_manager)
) -> dict:
    """List all active sessions."""
    sessions = session_manager.get_all_sessions()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "state": s.state.value,
                "created_at": s.created_at.isoformat(),
                "last_activity_at": s.last_activity_at.isoformat(),
                "audio_duration_ms": s.metrics.audio_duration_ms,
            }
            for s in sessions
        ],
        "count": len(sessions),
    }

@router.get("/metrics")
async def get_metrics(
    session_manager: SessionManager = Depends(get_session_manager)
) -> dict:
    """Get aggregated session metrics."""
    return session_manager.get_aggregate_metrics()

@router.delete("/{session_id}")
async def terminate_session(
    session_id: str,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Force terminate a session (admin use)."""
    try:
        await session_manager.close_session(session_id)
        return {"status": "closed", "session_id": session_id}
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="Session not found")
```

### 7. Updated Health Endpoint (`api/health.py`)

```python
@router.get("/v1/health")
async def health(
    session_manager: SessionManager = Depends(get_session_manager)
) -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "active_sessions": session_manager.get_active_count(),
    }
```

---

## Configuration Additions

```python
# config.py additions

class Settings(BaseSettings):
    # ... existing settings ...

    # Session management (Phase 2)
    max_sessions: int = 1000
    session_idle_timeout_seconds: float = 300.0
    session_cleanup_interval_seconds: float = 30.0

    class Config:
        env_prefix = "ASR_"
```

**Environment Variables:**
- `ASR_MAX_SESSIONS`: Maximum concurrent sessions per instance
- `ASR_SESSION_IDLE_TIMEOUT_SECONDS`: Idle timeout before cleanup
- `ASR_SESSION_CLEANUP_INTERVAL_SECONDS`: How often to run cleanup

---

## Test Plan

### Unit Tests: Session Lifecycle (8 tests)

```
test_session_lifecycle.py
├── test_session_starts_in_created_state
├── test_session_transitions_to_active_on_first_audio
├── test_session_transitions_to_closing_on_close
├── test_session_transitions_to_closed_after_cleanup
├── test_session_rejects_audio_when_closing
├── test_session_metrics_track_audio_bytes
├── test_session_metrics_track_transcripts
└── test_session_last_activity_updates_on_audio
```

### Unit Tests: Session Manager (12 tests)

```
test_session_manager.py
├── test_create_session_returns_new_session
├── test_create_session_assigns_unique_ids
├── test_create_session_respects_max_limit
├── test_create_session_raises_on_limit_exceeded
├── test_get_session_returns_existing_session
├── test_get_session_raises_on_not_found
├── test_close_session_removes_from_registry
├── test_close_session_calls_session_close
├── test_cleanup_removes_idle_sessions
├── test_cleanup_removes_closed_sessions
├── test_get_active_count_excludes_closing
└── test_get_aggregate_metrics_sums_correctly
```

### Integration Tests: Concurrent Sessions (6 tests)

```
test_concurrent.py
├── test_100_concurrent_websocket_connections
├── test_sessions_cleaned_up_on_disconnect
├── test_session_limit_returns_error
├── test_idle_timeout_closes_session
├── test_no_memory_leak_after_many_sessions
└── test_graceful_shutdown_closes_all_sessions
```

### API Tests: Session Endpoints (5 tests)

```
test_session_api.py
├── test_list_sessions_returns_active
├── test_get_metrics_returns_aggregates
├── test_terminate_session_closes_it
├── test_terminate_nonexistent_returns_404
└── test_health_includes_active_count
```

**Total New Tests: 31**

---

## Implementation Order

### Step 1: Session State & Metrics
- Add `SessionState` enum to `session.py`
- Add `SessionMetrics` dataclass
- Add `SessionInfo` dataclass
- Update `TranscriptionSession` with state tracking
- Add 8 lifecycle unit tests

### Step 2: Session Manager Core
- Create `session_manager.py` with `SessionManager` class
- Implement `create_session`, `get_session`, `close_session`
- Add session limit enforcement
- Add 8 manager unit tests

### Step 3: Background Cleanup
- Add `_cleanup_loop` and `_cleanup_idle_sessions`
- Integrate with `start()` / `stop()` lifecycle
- Add 4 cleanup unit tests

### Step 4: App Integration
- Update `main.py` lifespan to manage session manager
- Add `get_session_manager` dependency
- Update `stream.py` to use session manager
- Update `transcribe.py` to use session manager

### Step 5: Session Endpoints
- Create `api/sessions.py` with list/metrics/terminate
- Update `health.py` to include active count
- Add 5 API tests

### Step 6: Concurrent Testing
- Create `test_concurrent.py`
- Add 6 concurrency tests
- Verify 100 concurrent connections work
- Verify cleanup on disconnect

### Step 7: Configuration & Polish
- Add config settings for session limits/timeouts
- Add structured logging for session events
- Documentation updates

---

## Exit Criteria

- [ ] `SessionManager` tracks all sessions with unique IDs
- [ ] Session lifecycle states transition correctly
- [ ] 100 concurrent WebSocket connections work without issues
- [ ] Sessions clean up properly on client disconnect
- [ ] Idle sessions are automatically cleaned up after timeout
- [ ] `GET /v1/sessions` lists active sessions
- [ ] `GET /v1/sessions/metrics` returns aggregated metrics
- [ ] `GET /v1/health` includes active session count
- [ ] Max session limit is enforced with clear error
- [ ] No memory leaks over time (measured with 1000+ session cycles)
- [ ] All 31 new tests pass
- [ ] All 31 existing tests still pass

---

## Open Questions

1. **Should REST `/v1/transcribe` also use session tracking?**
   - **Recommendation**: Yes, but as short-lived sessions for metrics consistency
   - Decide: Track for metrics only, or skip session manager for simplicity?

2. **Session ID in response headers vs body?**
   - **Recommendation**: Body for WebSocket (already JSON), could add header for REST
   - Decide: Header `X-Session-ID` for REST responses?

3. **Admin authentication for `/v1/sessions` endpoints?**
   - **Recommendation**: Defer auth to Phase 5, but design endpoints with it in mind
   - Decide: Leave open or add placeholder middleware?

---

## Notes

- Session manager uses `asyncio.Lock` for thread-safety within a single event loop
- For multi-worker scenarios (Phase 3), session state would need externalization (Redis)
- Metrics are stored in-memory; for persistence, would need to export to Prometheus/StatsD

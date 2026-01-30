# Scalable Transcription Service - Backend Plan

## Purpose Shift

**Previous**: Single-customer real-time transcription demo with NeMo model
**New**: Learn how to build a **scalable, fast transcription backend** that can serve many concurrent users

This is a **learning/prototyping project** focused on architecture patterns, not model deployment.

---

## Objective

Build a **backend-only transcription service** that:

- Handles many concurrent users efficiently
- Maintains low latency under load
- Uses a **mocked model** (no GPU dependency)
- Demonstrates production-ready patterns for scaling

---

## Scope

### Included

- WebSocket server for streaming audio
- REST API for non-streaming use cases
- Mocked ASR model (returns fake transcriptions with realistic timing)
- Connection pooling and session management for multi-user
- Horizontal scaling patterns
- Load testing setup

### Excluded

- Real ML model deployment
- Frontend/UI
- Authentication (but design for it)
- Persistent storage

---

## High-Level Architecture

```
                    ┌─────────────────┐
                    │  Load Balancer  │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  ASR Worker   │   │  ASR Worker   │   │  ASR Worker   │
│  Instance 1   │   │  Instance 2   │   │  Instance N   │
├───────────────┤   ├───────────────┤   ├───────────────┤
│ - WebSocket   │   │ - WebSocket   │   │ - WebSocket   │
│ - REST API    │   │ - REST API    │   │ - REST API    │
│ - Mock Model  │   │ - Mock Model  │   │ - Mock Model  │
│ - Session Mgr │   │ - Session Mgr │   │ - Session Mgr │
└───────────────┘   └───────────────┘   └───────────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Redis/Memory   │
                    │  (Session State)│
                    └─────────────────┘
```

---

## API Design

### WebSocket Endpoint

```
WS /v1/transcribe/stream
```

**Client → Server Messages:**
```json
{ "type": "audio_chunk", "data": "<base64 audio>", "sequence": 1 }
{ "type": "stop" }
```

**Server → Client Messages:**
```json
{ "type": "partial", "text": "Hello wor", "timestamp": 1234567890 }
{ "type": "final", "text": "Hello world", "timestamp": 1234567891 }
{ "type": "error", "message": "...", "code": "..." }
```

### REST Endpoints

```
POST /v1/transcribe
  - Body: audio file
  - Response: { "text": "...", "duration_ms": ... }

GET /v1/health
  - Response: { "status": "ok", "active_sessions": N }

GET /v1/metrics
  - Response: { "total_requests": N, "avg_latency_ms": N, ... }
```

---

## Phased Delivery Plan

### Phase 1 — Mock Model & Core Service

**Goal**: Get a working service with fake transcription.

**Detailed plan**: [phase1-plan.md](./phase1-plan.md)

Deliverables:

- Mock ASR model that:
  - Accepts audio chunks
  - Uses real VAD (WebRTC) for silence detection
  - Returns fake text proportional to audio byte length
  - Independent fragments (client concatenates)

- Basic FastAPI/Starlette service with:
  - Health endpoint
  - Single WebSocket endpoint
  - Single REST endpoint

Exit criteria:
- Can connect via WebSocket and receive mock transcriptions
- VAD correctly distinguishes speech from silence
- Service starts in < 2 seconds

---

### Phase 2 — Session Management

**Goal**: Handle multiple concurrent users cleanly.

Deliverables:

- Session lifecycle:
  - CREATE → ACTIVE → CLOSING → CLOSED

- Session manager with:
  - Unique session IDs
  - Session timeout/cleanup
  - Max concurrent sessions limit
  - Graceful disconnect handling

- Per-session metrics:
  - Audio received
  - Transcripts sent
  - Duration

Exit criteria:
- 100 concurrent sessions without issues
- Sessions clean up properly on disconnect
- No memory leaks over time

---

### Phase 3 — Performance & Scaling Patterns

**Goal**: Make it fast and horizontally scalable.

Deliverables:

- Async everywhere (no blocking calls)
- Connection handling optimizations:
  - Backpressure on slow clients
  - Audio buffer limits

- Stateless worker design:
  - Session state externalized (Redis or in-memory for demo)
  - Any worker can handle any request

- Configuration for:
  - Worker count
  - Max sessions per worker
  - Timeouts

Exit criteria:
- Single worker handles 500+ concurrent connections
- Adding workers increases capacity linearly
- p99 latency < 100ms for mock responses

---

### Phase 4 — Load Testing & Observability

**Goal**: Prove it works under pressure.

Deliverables:

- Load test suite:
  - Simulated clients sending audio
  - Ramp-up tests
  - Sustained load tests

- Metrics endpoint exposing:
  - Active connections
  - Requests/second
  - Latency percentiles
  - Error rates

- Structured logging:
  - Request tracing
  - Session lifecycle events
  - Performance timings

Exit criteria:
- Documented capacity limits
- Graceful degradation under overload
- Clear visibility into system behavior

---

### Phase 5 — Production Readiness

**Goal**: Ready to swap in a real model.

Deliverables:

- Model interface abstraction:
  ```python
  class ASRModel(Protocol):
      async def transcribe_chunk(self, audio: bytes) -> TranscriptResult
      async def finalize(self) -> TranscriptResult
  ```

- Docker setup:
  - Multi-stage build
  - Health checks
  - Graceful shutdown

- Configuration management:
  - Environment variables
  - Sensible defaults

- Documentation:
  - API reference
  - Deployment guide
  - How to swap in real model

Exit criteria:
- `docker run` starts a working service
- Real model can be added by implementing one interface
- Service survives restarts cleanly

---

## Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Framework | FastAPI + Starlette | Async-native, WebSocket support |
| WebSocket | starlette.websockets | Built-in, performant |
| Session State | Redis (or in-memory dict) | Externalized, scalable |
| Load Testing | locust | Python-native, WebSocket support |
| Containerization | Docker | Standard deployment |

---

## Mock Model Specification

The mock model simulates realistic ASR behavior:

```python
class MockASRModel:
    def __init__(self, latency_ms: int = 50, words_per_chunk: int = 2):
        ...

    async def transcribe_chunk(self, audio: bytes) -> TranscriptResult:
        # Simulate processing delay
        await asyncio.sleep(self.latency_ms / 1000)
        # Return fake words based on audio length
        return TranscriptResult(
            text=self._generate_fake_text(),
            is_final=False
        )

    async def finalize(self) -> TranscriptResult:
        # Return complete "sentence"
        return TranscriptResult(
            text=self._get_accumulated_text(),
            is_final=True
        )
```

Configurable parameters:
- `latency_ms`: Simulated processing time
- `words_per_chunk`: How much text per audio chunk
- `error_rate`: Simulate occasional failures

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Concurrent connections | 1000+ per instance |
| WebSocket latency (p99) | < 100ms |
| REST latency (p99) | < 200ms |
| Memory per session | < 1MB |
| Startup time | < 3 seconds |

---

## Out of Scope (Future Considerations)

These are noted for future design but not implemented:

- Authentication/authorization hooks
- Rate limiting per user
- Audio format conversion
- Transcript storage/retrieval
- Multi-language support
- Real model integration

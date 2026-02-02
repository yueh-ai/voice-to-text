# Voice-to-Text Transcription Service

A scalable transcription service backend demonstrating production-ready patterns for handling concurrent users. Uses a mocked ASR model (no GPU required) to simulate real transcription while focusing on architecture, session management, and performance.

## Features

- **WebSocket Streaming** - Real-time audio streaming with partial/final transcription results
- **REST API** - Single-request transcription endpoint
- **Voice Activity Detection** - Real WebRTC VAD for speech detection
- **Session Management** - Centralized tracking with automatic cleanup
- **Configurable Limits** - Max concurrent sessions, idle timeouts
- **Load Testing** - Locust-based performance testing suite

## Quick Start

```bash
# Install dependencies
uv sync

# Run the server
uv run uvicorn transcription_service.main:app --reload

# Server runs at http://localhost:8001
```

## API Endpoints

### WebSocket Streaming

```
WS /v1/transcribe/stream
```

**Client → Server:**
```json
{ "type": "audio", "data": "<base64 PCM audio>" }
{ "type": "stop" }
```

**Server → Client:**
```json
{ "type": "session_start", "session_id": "uuid" }
{ "type": "partial", "text": "transcribed text" }
{ "type": "final" }
```

### REST Transcription

```bash
curl -X POST --data-binary @audio.pcm http://localhost:8001/v1/transcribe
```

Response:
```json
{ "text": "transcribed text", "duration_ms": 150 }
```

### Health Check

```bash
curl http://localhost:8001/v1/health
```

### Session Management

```bash
GET  /v1/sessions           # List active sessions
GET  /v1/sessions/metrics   # Aggregated metrics
DELETE /v1/sessions/{id}    # Terminate a session
```

## Configuration

Environment variables (prefix `ASR_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ASR_PORT` | 8001 | Server port |
| `ASR_MAX_SESSIONS` | 1000 | Max concurrent sessions |
| `ASR_SESSION_IDLE_TIMEOUT_SECONDS` | 300 | Idle session timeout |
| `ASR_VAD_AGGRESSIVENESS` | 2 | VAD sensitivity (0-3) |
| `ASR_LATENCY_MS` | 50 | Simulated processing delay |

## Testing

```bash
# Run all tests
uv run pytest

# With coverage
uv run pytest --cov=src/transcription_service

# Specific test file
uv run pytest tests/test_session_manager.py -v
```

## Load Testing

```bash
# Start the service first, then:
cd loadtests
uv run locust --host=http://localhost:8001

# Headless mode
uv run locust --host=http://localhost:8001 --headless -u 100 -r 10 -t 60s
```

## Project Structure

```
voice-to-text/
├── src/transcription_service/
│   ├── main.py              # FastAPI app
│   ├── config.py            # Settings
│   ├── api/
│   │   ├── health.py        # Health endpoint
│   │   ├── transcribe.py    # REST transcription
│   │   ├── stream.py        # WebSocket streaming
│   │   └── sessions.py      # Session management
│   └── core/
│       ├── models.py        # Shared model container
│       ├── session.py       # Per-user session
│       ├── session_manager.py
│       ├── vad.py           # Voice activity detection
│       └── mock_asr.py      # Mock transcription
├── tests/                   # Test suite
├── loadtests/               # Locust load tests
└── pyproject.toml
```

## Architecture

```
┌─────────────────────────────────────────┐
│        Shared Models (Singleton)        │
│   VADModel (stateless)  MockASRModel    │
└─────────────────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
   SessionManager         Dependencies
   (centralized)          (FastAPI DI)
         │
    ┌────┴────┬────────┐
    ▼         ▼        ▼
 Session1  Session2  SessionN
(per-user state, metrics, lifecycle)
```

## Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Mock Model & Core Service | Complete |
| 2 | Session Management | Complete |
| 3 | Performance & Scaling | Planned |
| 4 | Observability | Planned |
| 5 | Production Readiness | Planned |

## License

MIT

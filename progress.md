# Project Progress

**Last Updated:** 2026-01-31

## Overview

This is a **voice-to-text transcription service backend** project focused on learning scalable backend patterns with a mocked ASR (Automatic Speech Recognition) model.

## Current Phase: Shared Model Architecture Refactor ✅

Refactored architecture to separate **model weights** (shared singleton) from **inference state** (per-user session). This prepares the codebase for real GPU models in future phases.

| Aspect | Status |
|--------|--------|
| Planning Documents | ✅ Complete |
| Test Definitions | ✅ Complete (31 tests) |
| Source Code | ✅ Implemented |
| Tests Passing | ✅ All 31 passing |

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│            Shared Models (Singleton)                │
│   ┌─────────────┐           ┌─────────────┐        │
│   │  VADModel   │           │  ASRModel   │        │
│   │  (weights)  │           │  (weights)  │        │
│   └─────────────┘           └─────────────┘        │
└─────────────────────────────────────────────────────┘
              │                       │
    ┌─────────┼───────────────────────┼─────────┐
    ▼         ▼                       ▼         ▼
┌────────┐ ┌────────┐           ┌────────┐ ┌────────┐
│Session1│ │Session2│    ...    │SessionN│ │SessionM│
│-vad_ctx│ │-vad_ctx│           │-vad_ctx│ │-vad_ctx│
│-buffer │ │-buffer │           │-buffer │ │-buffer │
└────────┘ └────────┘           └────────┘ └────────┘
```

## Project Structure

```
src/transcription_service/
├── __init__.py           # Package with version
├── main.py               # FastAPI app with lifespan (model init)
├── config.py             # Pydantic settings
├── api/
│   ├── __init__.py
│   ├── health.py         # GET /v1/health
│   ├── transcribe.py     # POST /v1/transcribe
│   └── stream.py         # WS /v1/transcribe/stream
└── core/
    ├── __init__.py
    ├── models.py         # Shared Models container (NEW)
    ├── session.py        # TranscriptionSession per-user state (NEW)
    ├── vad.py            # VADModel (shared) + VADSession (per-user)
    ├── mock_asr.py       # MockASRModel (stateless)
    └── text_generator.py # Fake text generation
```

## Components Implemented

### Shared Models (Singleton)

1. **Models Container (`core/models.py`)**
   - [x] `Models` dataclass holding VAD and ASR models
   - [x] `init_models(config)` - Load at app startup
   - [x] `get_models()` - FastAPI dependency for access

2. **VADModel (`core/vad.py`)**
   - [x] Shared WebRTC VAD instance (stateless)
   - [x] `is_speech(frame)` - Inference on single frame

3. **MockASRModel (`core/mock_asr.py`)**
   - [x] Stateless text generation
   - [x] `transcribe(audio)` - Async with latency
   - [x] `transcribe_sync(audio)` - Sync version

### Per-User Sessions

4. **TranscriptionSession (`core/session.py`)**
   - [x] Holds per-user VADSession
   - [x] Tracks speech buffer and silence duration
   - [x] `process_chunk(audio)` - Streaming transcription
   - [x] `transcribe_full(audio)` - Full file transcription

5. **VADSession (`core/vad.py`)**
   - [x] Per-user audio buffer
   - [x] Delegates inference to shared VADModel

### API Endpoints

6. **App Startup (`main.py`)**
   - [x] FastAPI lifespan for model initialization
   - [x] Models loaded once at startup

7. **Endpoints (Updated)**
   - [x] Use `get_models()` for shared singleton
   - [x] Create lightweight `TranscriptionSession` per request/connection

## Test Status

```
Total Tests: 31
Passing: 31
Failing: 0
```

### Original Tests (9)
- `test_health_returns_ok_status` ✅
- `test_health_includes_version` ✅
- `test_stream_connection_succeeds` ✅
- `test_stream_stop_command_closes_cleanly` ✅
- `test_stream_audio_returns_response` ✅
- `test_stream_returns_partial_with_text` ✅
- `test_transcribe_returns_text_for_audio` ✅
- `test_transcribe_returns_duration` ✅
- `test_transcribe_rejects_empty_audio` ✅

### New Tests - VADModel/VADSession (10)
- `test_model_is_stateless` ✅
- `test_model_is_speech_on_single_frame` ✅
- `test_model_validates_sample_rate` ✅
- `test_model_validates_aggressiveness` ✅
- `test_session_has_buffer` ✅
- `test_session_accumulates_buffer` ✅
- `test_session_returns_true_when_buffer_insufficient` ✅
- `test_session_delegates_to_model` ✅
- `test_session_reset_clears_buffer` ✅
- `test_multiple_sessions_share_model` ✅

### New Tests - Models Container (5)
- `test_models_container_holds_vad_and_asr` ✅
- `test_init_models_creates_singleton` ✅
- `test_get_models_raises_before_init` ✅
- `test_init_models_creates_vad_model` ✅
- `test_init_models_creates_asr_model` ✅

### New Tests - TranscriptionSession (7)
- `test_session_created_with_models_and_config` ✅
- `test_session_has_vad_session` ✅
- `test_session_has_per_user_state` ✅
- `test_process_chunk_returns_transcript_result` ✅
- `test_process_chunk_generates_partial_for_speech` ✅
- `test_multiple_sessions_have_isolated_state` ✅
- `test_transcribe_full_uses_shared_model` ✅

## 5-Phase Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Mock Model & Core Service | ✅ **Complete** |
| 1.5 | Shared Model Architecture | ✅ **Complete** |
| 2 | Session Management | Planned |
| 3 | Performance & Scaling | Planned |
| 4 | Load Testing & Observability | Planned |
| 5 | Production Readiness | Planned |

## Benefits of New Architecture

1. **Memory Efficient**: Single model instance regardless of connection count
2. **Fast Startup**: Models loaded once, sessions are lightweight
3. **Scalable**: Ready for real GPU models (Silero VAD, Whisper/Canary ASR)
4. **Testable**: Clean separation allows mocking at model or session level
5. **Maintainable**: Clear distinction between shared and per-user state

## Next Steps (Phase 2)

Phase 2 will focus on session management:
- Multiple concurrent users stress testing
- Session lifecycle management
- Resource cleanup and connection limits
- Real model integration (swap mock for Silero/Whisper)

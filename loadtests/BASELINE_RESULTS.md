# Baseline Performance Results

**Date**: 2026-01-31
**Phase**: 2 (Session Management) - Before Phase 3 optimizations

## Test Environment

- Single uvicorn worker (default)
- Default settings (max 1000 sessions)
- Locust load generator (single process)

## Summary

| Metric | Target (plan.md) | Baseline | Status |
|--------|------------------|----------|--------|
| Concurrent connections | 1000+ per instance | 300 tested, 0 failures | Partial |
| WebSocket latency (p99) | < 100ms | 67-87ms @ 100-200 users | **PASS** |
| REST latency (p99) | < 200ms | 120-140ms @ 100-200 users | **PASS** |
| Memory per session | < 1MB | Not measured | - |
| Startup time | < 3 seconds | < 1 second | **PASS** |

## Detailed Results

### Test 1: 100 Users (Mixed Workload)

```
Duration: 60s
Users: 100 (TranscriptionUser)

Endpoint              | Requests | Failures | Avg (ms) | p99 (ms) | RPS
----------------------|----------|----------|----------|----------|------
WebSocket audio_chunk |   89,590 |     0    |    51    |    67    | 1,495
WebSocket connect     |      638 |     0    |     6    |   110    |   10
REST /v1/transcribe   |      280 |     0    |     9    |   120    |    5
Health check          |       78 |     0    |     3    |    51    |    1
```

**Key findings:**
- Zero failures
- WebSocket p99 latency: 67ms (includes 50ms mock ASR delay)
- REST p99 latency: 120ms
- Throughput: ~1,500 req/s (primarily WebSocket chunks)

### Test 2: 200 Users (Mixed Workload)

```
Duration: 45s
Users: 200 (TranscriptionUser)

Endpoint              | Requests | Failures | Avg (ms) | p99 (ms) | RPS
----------------------|----------|----------|----------|----------|------
WebSocket audio_chunk |  121,265 |     0    |    54    |    86    | 2,697
WebSocket connect     |      903 |     0    |    13    |   160    |   20
REST /v1/transcribe   |      416 |     0    |    17    |   140    |    9
Health check          |      133 |     0    |    12    |   140    |    3
```

**Key findings:**
- Zero failures at 200 concurrent users
- Latency increase is minimal
- Throughput: ~2,700 req/s

### Test 3: 300 Users (All User Types)

```
Duration: 30s
Users: 300 (100 each: WebSocketOnly, RESTOnly, TranscriptionUser)

Endpoint              | Requests | Failures | Avg (ms) | p99 (ms) | RPS
----------------------|----------|----------|----------|----------|------
WebSocket audio_chunk |   27,084 |     0    |   190    |   600    |  902
WebSocket connect     |      250 |     0    |   198    |   750    |    8
REST /v1/transcribe   |    3,233 |     0    |   370    |   770    |  108
Health check          |       27 |     0    |   220    |   530    |    1
```

**Key findings:**
- Zero failures but latency increased significantly
- **WARNING: Locust CPU > 90%** - Client bottleneck, not server
- p99 latencies 4-6x higher than at 200 users
- Need distributed load testing for accurate 300+ user benchmarks

## Performance Characteristics

### Latency Breakdown (WebSocket)

The WebSocket `audio_chunk` latency includes:
1. **Mock ASR processing**: ~50ms (configured delay)
2. **VAD processing**: < 1ms
3. **Network/framework overhead**: 1-3ms
4. **Under load**: +10-50ms queueing

### Throughput

| Load Level | WebSocket RPS | Total RPS |
|------------|---------------|-----------|
| 100 users  | 1,495         | ~1,520    |
| 200 users  | 2,697         | ~2,750    |
| 300 users* | 902           | ~1,020    |

*Bottlenecked by Locust client CPU

### Observations

1. **Session management overhead is minimal** - No visible performance degradation from session lifecycle tracking

2. **Single worker handles 200+ connections** - Current architecture scales well up to this point

3. **Latency target met at realistic loads** - p99 < 100ms for WebSocket at 100-200 users

4. **Load generator limits reached** - Need distributed Locust or separate machines for higher loads

## Recommendations for Phase 3

1. **Multi-worker testing** - Run uvicorn with `--workers 4` and retest
2. **Memory profiling** - Measure per-session memory usage
3. **Connection pooling** - Consider connection limits per worker
4. **Backpressure** - Implement for slow clients at high concurrency
5. **Distributed load testing** - Use Locust master/worker for 500+ users

## Files

- `locustfile.py` - Load test definitions
- `run_baseline.sh` - Convenience script
- `results/` - CSV and HTML reports (when using `--csv` or `--html`)

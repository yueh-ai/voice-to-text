"""
Load tests for the transcription service.

Tests both WebSocket streaming and REST endpoints.

Run with:
    cd loadtests
    locust --host=http://localhost:8000

Or headless:
    locust --host=http://localhost:8000 --headless -u 100 -r 10 -t 60s

Environment variables:
    LOAD_TEST_AUDIO: auto (default) | real | synthetic
        auto     - Use real clips if available, fall back to synthetic
        real     - Require real clips (error if missing)
        synthetic - Always use synthetic random PCM
    LOAD_TEST_STREAM_PACE: realtime | fast | none (default)
        realtime - 20ms delay per 20ms chunk (1x real-time)
        fast     - 5ms delay per chunk (~4x real-time)
        none     - No delay (max throughput)
"""

import base64
import json
import os
import sys
import time
import random

from locust import HttpUser, task, between, events
from locust.runners import MasterRunner
import websocket

from audio.audio_provider import get_provider, AudioProvider


# --- Audio mode configuration ---

AUDIO_MODE = os.environ.get("LOAD_TEST_AUDIO", "auto").lower()
STREAM_PACE = os.environ.get("LOAD_TEST_STREAM_PACE", "none").lower()

PACE_DELAYS = {
    "realtime": 0.020,  # 20ms - matches chunk duration
    "fast": 0.005,      # 5ms - ~4x real-time
    "none": 0.0,        # No delay
}

if STREAM_PACE not in PACE_DELAYS:
    print(f"WARNING: Unknown LOAD_TEST_STREAM_PACE={STREAM_PACE!r}, using 'none'")
    STREAM_PACE = "none"

PACE_DELAY = PACE_DELAYS[STREAM_PACE]

# Initialize audio provider
provider = get_provider()

if AUDIO_MODE == "real" and not provider.is_real:
    print("ERROR: LOAD_TEST_AUDIO=real but no audio clips found.")
    print("Run: python loadtests/audio/download_audio.py")
    sys.exit(1)
elif AUDIO_MODE == "synthetic":
    # Force synthetic even if clips exist
    provider = AudioProvider()  # Fresh instance with no clips loaded
    provider.is_real = False
elif AUDIO_MODE not in ("auto", "real", "synthetic"):
    print(f"WARNING: Unknown LOAD_TEST_AUDIO={AUDIO_MODE!r}, using 'auto'")
    AUDIO_MODE = "auto"


class WebSocketClient:
    """WebSocket client for load testing."""

    def __init__(self, host: str):
        self.host = host
        self.ws = None
        self.session_id = None

    def connect(self) -> float:
        """Connect to WebSocket and return connection time in ms."""
        ws_url = self.host.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/v1/transcribe/stream"

        start = time.perf_counter()
        self.ws = websocket.create_connection(ws_url, timeout=10)

        # Wait for session_start message
        response = json.loads(self.ws.recv())
        elapsed_ms = (time.perf_counter() - start) * 1000

        if response.get("type") == "session_start":
            self.session_id = response.get("session_id")
        elif response.get("type") == "error":
            raise Exception(f"Connection error: {response.get('message')}")

        return elapsed_ms

    def send_audio(self, audio_bytes: bytes) -> tuple[dict, float]:
        """Send audio chunk and wait for response. Returns (response, latency_ms)."""
        if not self.ws:
            raise Exception("Not connected")

        message = {
            "type": "audio",
            "data": base64.b64encode(audio_bytes).decode("utf-8"),
        }

        start = time.perf_counter()
        self.ws.send(json.dumps(message))
        response = json.loads(self.ws.recv())
        elapsed_ms = (time.perf_counter() - start) * 1000

        return response, elapsed_ms

    def close(self):
        """Close the connection."""
        if self.ws:
            try:
                self.ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.session_id = None


class TranscriptionUser(HttpUser):
    """
    Load test user that tests both REST and WebSocket endpoints.

    Behavior:
    - 70% WebSocket streaming sessions (send multiple audio chunks)
    - 30% REST single transcriptions
    """

    wait_time = between(0.5, 2.0)  # Wait between tasks

    @task(7)
    def websocket_streaming(self):
        """Simulate a WebSocket streaming session."""
        client = WebSocketClient(self.host)

        # Connect
        try:
            connect_time = client.connect()
            events.request.fire(
                request_type="WebSocket",
                name="connect",
                response_time=connect_time,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as e:
            events.request.fire(
                request_type="WebSocket",
                name="connect",
                response_time=0,
                response_length=0,
                exception=e,
                context={},
            )
            return

        # Get audio chunks from provider
        chunks = provider.get_streaming_chunks()
        chunk_latencies = []

        try:
            for chunk in chunks:
                try:
                    response, latency = client.send_audio(chunk)
                    chunk_latencies.append(latency)

                    if response.get("type") == "error":
                        events.request.fire(
                            request_type="WebSocket",
                            name="audio_chunk",
                            response_time=latency,
                            response_length=0,
                            exception=Exception(response.get("message")),
                            context={},
                        )
                    else:
                        events.request.fire(
                            request_type="WebSocket",
                            name="audio_chunk",
                            response_time=latency,
                            response_length=len(str(response)),
                            exception=None,
                            context={},
                        )
                except Exception as e:
                    events.request.fire(
                        request_type="WebSocket",
                        name="audio_chunk",
                        response_time=0,
                        response_length=0,
                        exception=e,
                        context={},
                    )
                    break

                if PACE_DELAY > 0:
                    time.sleep(PACE_DELAY)

        finally:
            client.close()

        # Report session stats
        if chunk_latencies:
            avg_latency = sum(chunk_latencies) / len(chunk_latencies)
            events.request.fire(
                request_type="WebSocket",
                name="session_complete",
                response_time=avg_latency,
                response_length=len(chunks),
                exception=None,
                context={},
            )

    @task(3)
    def rest_transcribe(self):
        """Simulate a REST transcription request."""
        audio = provider.get_rest_audio()

        with self.client.post(
            "/v1/transcribe",
            data=audio,
            headers={"Content-Type": "application/octet-stream"},
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 503:
                response.failure("Session limit exceeded")
            else:
                response.failure(f"Status {response.status_code}: {response.text}")

    @task(1)
    def health_check(self):
        """Check health endpoint."""
        self.client.get("/v1/health")


class WebSocketOnlyUser(HttpUser):
    """
    User that only tests WebSocket connections.

    Use this for focused WebSocket load testing:
        locust -f locustfile.py WebSocketOnlyUser --host=http://localhost:8000
    """

    wait_time = between(0.1, 0.5)

    @task
    def websocket_streaming(self):
        """Single WebSocket streaming session."""
        client = WebSocketClient(self.host)

        try:
            connect_time = client.connect()
            events.request.fire(
                request_type="WebSocket",
                name="connect",
                response_time=connect_time,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as e:
            events.request.fire(
                request_type="WebSocket",
                name="connect",
                response_time=0,
                response_length=0,
                exception=e,
                context={},
            )
            return

        # Get audio chunks from provider
        chunks = provider.get_streaming_chunks()

        try:
            for chunk in chunks:
                try:
                    response, latency = client.send_audio(chunk)
                    events.request.fire(
                        request_type="WebSocket",
                        name="audio_chunk",
                        response_time=latency,
                        response_length=len(str(response)),
                        exception=None if response.get("type") != "error" else Exception(response.get("message")),
                        context={},
                    )
                except Exception as e:
                    events.request.fire(
                        request_type="WebSocket",
                        name="audio_chunk",
                        response_time=0,
                        response_length=0,
                        exception=e,
                        context={},
                    )
                    break

                if PACE_DELAY > 0:
                    time.sleep(PACE_DELAY)
        finally:
            client.close()


class RESTOnlyUser(HttpUser):
    """
    User that only tests REST endpoint.

    Use this for focused REST load testing:
        locust -f locustfile.py RESTOnlyUser --host=http://localhost:8000
    """

    wait_time = between(0.1, 0.5)

    @task
    def rest_transcribe(self):
        """REST transcription request."""
        audio = provider.get_rest_audio()

        with self.client.post(
            "/v1/transcribe",
            data=audio,
            headers={"Content-Type": "application/octet-stream"},
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 503:
                response.failure("Session limit exceeded")
            else:
                response.failure(f"Status {response.status_code}: {response.text}")


# Event hooks for statistics
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("Load test starting...")
    print(f"Target host: {environment.host}")
    print(f"Audio mode: {provider.mode}")
    if provider.is_real:
        print(f"Real audio clips: {provider.clip_count}")
    else:
        print("Using synthetic random PCM audio")
    print(f"Stream pacing: {STREAM_PACE} (delay={PACE_DELAY*1000:.0f}ms/chunk)")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\nLoad test completed!")

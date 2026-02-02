"""
Load tests for the transcription service.

Tests both WebSocket streaming and REST endpoints.

Run with:
    cd loadtests
    locust --host=http://localhost:8000

Or headless:
    locust --host=http://localhost:8000 --headless -u 100 -r 10 -t 60s
"""

import base64
import json
import time
import random
from contextlib import contextmanager

from locust import HttpUser, task, between, events
from locust.runners import MasterRunner
import websocket


# Generate fake PCM audio data (16-bit, 16kHz mono)
# 20ms of audio = 640 bytes (20ms * 16000Hz * 2 bytes)
def generate_audio_chunk(duration_ms: int = 20) -> bytes:
    """Generate fake PCM audio data."""
    samples = int(16000 * duration_ms / 1000)
    # Generate random audio-like data (not silence, to trigger VAD)
    return bytes(random.randint(0, 255) for _ in range(samples * 2))


# Pre-generate some audio chunks to avoid overhead during tests
AUDIO_CHUNKS = [generate_audio_chunk(20) for _ in range(100)]


def get_random_audio_chunk() -> bytes:
    """Get a pre-generated audio chunk."""
    return random.choice(AUDIO_CHUNKS)


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

        # Send audio chunks (simulate 1-5 seconds of audio)
        num_chunks = random.randint(50, 250)  # 1-5 seconds at 20ms chunks
        chunk_latencies = []

        try:
            for i in range(num_chunks):
                audio = get_random_audio_chunk()
                try:
                    response, latency = client.send_audio(audio)
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

        finally:
            client.close()

        # Report session stats
        if chunk_latencies:
            avg_latency = sum(chunk_latencies) / len(chunk_latencies)
            events.request.fire(
                request_type="WebSocket",
                name="session_complete",
                response_time=avg_latency,
                response_length=num_chunks,
                exception=None,
                context={},
            )

    @task(3)
    def rest_transcribe(self):
        """Simulate a REST transcription request."""
        # Generate 1-3 seconds of audio
        duration_ms = random.randint(1000, 3000)
        audio = generate_audio_chunk(duration_ms)

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

        # Longer sessions for stress testing
        num_chunks = random.randint(100, 500)

        try:
            for _ in range(num_chunks):
                audio = get_random_audio_chunk()
                try:
                    response, latency = client.send_audio(audio)
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
        duration_ms = random.randint(500, 2000)
        audio = generate_audio_chunk(duration_ms)

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


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\nLoad test completed!")

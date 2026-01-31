"""WebSocket streaming transcription endpoint."""

import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from transcription_service.config import get_settings
from transcription_service.core.mock_asr import MockASRModel

router = APIRouter()


@router.websocket("/v1/transcribe/stream")
async def stream(websocket: WebSocket):
    """
    WebSocket streaming transcription endpoint.

    Client -> Server:
        { "type": "audio", "data": "<base64 PCM audio>" }
        { "type": "stop" }

    Server -> Client:
        { "type": "partial", "text": "hello world" }
        { "type": "final" }
        { "type": "error", "message": "...", "code": "..." }
    """
    await websocket.accept()

    config = get_settings()
    asr = MockASRModel(config)

    try:
        while True:
            # Receive message
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                    "code": "INVALID_JSON",
                })
                continue

            msg_type = message.get("type")

            if msg_type == "stop":
                # Client requested stop, close connection
                await websocket.close()
                break

            elif msg_type == "audio":
                # Decode base64 audio
                audio_b64 = message.get("data", "")
                try:
                    audio_bytes = base64.b64decode(audio_b64)
                except Exception:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Invalid base64 audio data",
                        "code": "INVALID_AUDIO",
                    })
                    continue

                if not audio_bytes:
                    continue

                # Process audio chunk
                result = await asr.process_chunk(audio_bytes)

                if result.is_final:
                    await websocket.send_json({"type": "final"})
                else:
                    await websocket.send_json({
                        "type": "partial",
                        "text": result.text,
                    })

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                    "code": "UNKNOWN_TYPE",
                })

    except WebSocketDisconnect:
        # Client disconnected, clean up
        pass

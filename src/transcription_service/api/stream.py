"""WebSocket streaming transcription endpoint."""

import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from transcription_service.dependencies import get_session_manager
from transcription_service.core.session_manager import SessionLimitExceeded
from transcription_service.core.session import SessionClosingError

router = APIRouter()


@router.websocket("/v1/transcribe/stream")
async def stream(websocket: WebSocket):
    """
    WebSocket streaming transcription endpoint.

    Client -> Server:
        { "type": "audio", "data": "<base64 PCM audio>" }
        { "type": "stop" }

    Server -> Client:
        { "type": "session_start", "session_id": "..." }
        { "type": "partial", "text": "hello world" }
        { "type": "final" }
        { "type": "error", "message": "...", "code": "..." }
    """
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
            "session_id": session.get_info().session_id,
        })

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
                try:
                    result = await session.process_chunk(audio_bytes)
                except SessionClosingError:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Session is closing",
                        "code": "SESSION_CLOSING",
                    })
                    break

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
    finally:
        # Always clean up
        if session:
            await session_manager.close_session(session.get_info().session_id)

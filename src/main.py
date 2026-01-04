from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import logging
import uuid
import json
from typing import Dict, Any

from src.session import SessionManager, SessionState

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Real-Time Transcription Service")
session_manager = SessionManager()


@app.get("/")
async def root():
    return {"status": "ok", "service": "real-time-transcription"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.websocket("/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    session = None

    try:
        session = await session_manager.create_session(session_id)

        await websocket.send_json({
            "type": "session_started",
            "session_id": session_id,
            "state": session.get_state().value
        })

        logger.info(f"WebSocket connected: session {session_id}")

        while True:
            try:
                data = await websocket.receive()

                if "text" in data:
                    message = json.loads(data["text"])
                    await handle_text_message(websocket, session, message)

                elif "bytes" in data:
                    await handle_audio_data(websocket, session, data["bytes"])

            except WebSocketDisconnect:
                logger.info(f"Client disconnected: session {session_id}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Internal server error"
            })
        except:
            pass
    finally:
        if session:
            await session.finalize()
            await session_manager.close_session(session_id)
        try:
            await websocket.close()
        except:
            pass
        logger.info(f"Session {session_id} cleanup complete")


async def handle_text_message(websocket: WebSocket, session, message: Dict[str, Any]):
    msg_type = message.get("type")

    if msg_type == "start":
        if session.get_state() == SessionState.INIT:
            await session.start_streaming()
            await websocket.send_json({
                "type": "streaming_started",
                "session_id": session.session_id,
                "state": session.get_state().value
            })
        else:
            await websocket.send_json({
                "type": "error",
                "message": f"Cannot start from state {session.get_state().value}"
            })

    elif msg_type == "stop":
        await session.finalize()
        await websocket.send_json({
            "type": "streaming_stopped",
            "session_id": session.session_id,
            "state": session.get_state().value,
            "final_transcript": ""
        })
        await session.close()

    else:
        await websocket.send_json({
            "type": "error",
            "message": f"Unknown message type: {msg_type}"
        })


async def handle_audio_data(websocket: WebSocket, session, audio_bytes: bytes):
    if session.get_state() != SessionState.STREAMING:
        logger.warning(f"Received audio in state {session.get_state()}, ignoring")
        return

    try:
        await session.add_audio_chunk(audio_bytes)

        await websocket.send_json({
            "type": "partial_transcript",
            "text": f"[Received {len(audio_bytes)} bytes]",
            "is_partial": True
        })

    except Exception as e:
        logger.error(f"Error handling audio: {e}")
        await websocket.send_json({
            "type": "error",
            "message": "Error processing audio"
        })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

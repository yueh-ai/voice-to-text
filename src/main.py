from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import logging
import uuid
import json
from typing import Dict, Any

from src.session import SessionManager, SessionState
from src.config import Config
from src.asr_engine import ASREngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Real-Time Transcription Service")

# Global state for ASR engine and config
config = None
asr_engine_instance = None
session_manager = None


@app.on_event("startup")
async def startup_event():
    """Initialize ASR engine on startup"""
    global config, asr_engine_instance, session_manager

    try:
        logger.info("Starting ASR service initialization...")

        # Load configuration
        config = Config.load()
        logger.info("Configuration loaded")

        # Get ASR engine instance
        asr_engine_instance = await ASREngine.get_instance()

        # Load the model
        await asr_engine_instance.load_model(config)

        # Initialize session manager with ASR components
        session_manager = SessionManager(asr_engine_instance, config)

        # Mark as ready
        app.state.asr_ready = True
        logger.info("✓ ASR service ready")

    except Exception as e:
        app.state.asr_ready = False
        app.state.asr_error = str(e)
        logger.error(f"✗ ASR initialization failed: {e}")
        logger.warning("Service running in degraded mode - transcription unavailable")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown"""
    logger.info("Shutting down ASR service...")

    if asr_engine_instance:
        await asr_engine_instance.cleanup()

    logger.info("Shutdown complete")


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "real-time-transcription",
        "asr_ready": getattr(app.state, "asr_ready", False)
    }


@app.get("/health")
async def health():
    """Health check with ASR status"""
    asr_ready = getattr(app.state, "asr_ready", False)

    health_status = {
        "status": "healthy" if asr_ready else "degraded",
        "asr_available": asr_ready
    }

    if not asr_ready:
        health_status["error"] = getattr(app.state, "asr_error", "Unknown error")

    if asr_ready and asr_engine_instance:
        health_status["asr_stats"] = asr_engine_instance.get_stats()

    return health_status


@app.websocket("/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    await websocket.accept()

    # Check if ASR is available
    if not getattr(app.state, "asr_ready", False):
        await websocket.send_json({
            "type": "error",
            "message": "ASR service unavailable",
            "details": getattr(app.state, "asr_error", "Service not initialized"),
            "suggestion": "Check GPU availability and model installation"
        })
        await websocket.close()
        return

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

        # Get final transcript
        final_transcript = session.get_final_transcript()

        await websocket.send_json({
            "type": "streaming_stopped",
            "session_id": session.session_id,
            "state": session.get_state().value,
            "final_transcript": final_transcript
        })
        await session.close()

    else:
        await websocket.send_json({
            "type": "error",
            "message": f"Unknown message type: {msg_type}"
        })


async def handle_audio_data(websocket: WebSocket, session, audio_bytes: bytes):
    """Process audio data and send transcription results"""
    if session.get_state() != SessionState.STREAMING:
        logger.warning(f"Received audio in state {session.get_state()}, ignoring")
        return

    try:
        # Process audio and get transcription results
        results = await session.add_audio_chunk(audio_bytes)

        # Send all transcript results to client
        for result in results:
            await websocket.send_json(result)

        # Debug logging
        if results:
            logger.debug(f"Processed {len(audio_bytes)} bytes → {len(results)} results")

    except RuntimeError as e:
        # ASR-specific errors (model not loaded, GPU OOM, etc.)
        logger.error(f"ASR error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": "ASR processing error",
            "details": str(e)
        })
    except Exception as e:
        logger.error(f"Error handling audio: {e}", exc_info=True)
        await websocket.send_json({
            "type": "error",
            "message": "Error processing audio"
        })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

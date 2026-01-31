"""REST transcription endpoint."""

from fastapi import APIRouter, Request, HTTPException

from transcription_service.dependencies import get_session_manager
from transcription_service.core.session_manager import SessionLimitExceeded

router = APIRouter()


@router.post("/v1/transcribe")
async def transcribe(request: Request):
    """
    Transcribe audio data.

    Accepts raw PCM audio bytes and returns fake transcription.
    Uses session manager for consistent tracking and limit enforcement.
    """
    audio_data = await request.body()

    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio data")

    session_manager = get_session_manager()

    try:
        session = await session_manager.create_session()
    except SessionLimitExceeded as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        result = session.transcribe_full(audio_data)
    finally:
        # Always clean up - REST sessions are short-lived
        await session_manager.close_session(session.get_info().session_id)

    return {
        "text": result.text,
        "duration_ms": result.duration_ms,
    }

"""REST transcription endpoint."""

from fastapi import APIRouter, Request, HTTPException

from transcription_service.config import get_settings
from transcription_service.core.models import get_models
from transcription_service.core.session import TranscriptionSession

router = APIRouter()


@router.post("/v1/transcribe")
async def transcribe(request: Request):
    """
    Transcribe audio data.

    Accepts raw PCM audio bytes and returns fake transcription.
    """
    # Read raw body
    audio_data = await request.body()

    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio data")

    # Use shared models via session
    config = get_settings()
    models = get_models()
    session = TranscriptionSession(models, config)
    result = session.transcribe_full(audio_data)

    return {
        "text": result.text,
        "duration_ms": result.duration_ms,
    }

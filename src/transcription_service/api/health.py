"""Health check endpoint."""

from fastapi import APIRouter

from transcription_service import __version__
from transcription_service.dependencies import get_session_manager

router = APIRouter()


@router.get("/v1/health")
async def health():
    """Return health status, version, and session information."""
    session_manager = get_session_manager()
    return {
        "status": "ok",
        "version": __version__,
        "active_sessions": session_manager.get_active_count(),
    }

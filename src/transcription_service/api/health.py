"""Health check endpoint."""

from fastapi import APIRouter

from transcription_service import __version__

router = APIRouter()


@router.get("/v1/health")
async def health():
    """Return health status and version information."""
    return {
        "status": "ok",
        "version": __version__,
    }

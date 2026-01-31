"""Session inspection and management endpoints."""

from fastapi import APIRouter, HTTPException

from transcription_service.dependencies import get_session_manager

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.get("")
async def list_sessions():
    """List all active sessions."""
    session_manager = get_session_manager()
    sessions = session_manager.get_all_sessions()

    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "state": s.state.value,
                "created_at": s.created_at.isoformat(),
                "last_activity_at": s.last_activity_at.isoformat(),
                "audio_duration_ms": s.metrics.audio_duration_ms,
            }
            for s in sessions
        ],
        "count": len(sessions),
    }


@router.get("/metrics")
async def get_metrics():
    """Get aggregated session metrics."""
    session_manager = get_session_manager()
    return session_manager.get_aggregate_metrics()


@router.delete("/{session_id}")
async def terminate_session(session_id: str):
    """Force terminate a session (admin use)."""
    session_manager = get_session_manager()

    # close_session is atomic - returns True if found and closed
    closed = await session_manager.close_session(session_id)

    if not closed:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "closed", "session_id": session_id}

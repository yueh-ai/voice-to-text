"""Application-wide dependencies and state management.

This module is separate from main.py to avoid circular imports.
"""

from typing import Optional

from transcription_service.core.session_manager import SessionManager


# Global session manager instance
_session_manager: Optional[SessionManager] = None


def set_session_manager(manager: SessionManager) -> None:
    """Set the global session manager instance."""
    global _session_manager
    _session_manager = manager


def clear_session_manager() -> None:
    """Clear the global session manager instance."""
    global _session_manager
    _session_manager = None


def get_session_manager() -> SessionManager:
    """Get the session manager instance."""
    if _session_manager is None:
        raise RuntimeError("Session manager not initialized")
    return _session_manager

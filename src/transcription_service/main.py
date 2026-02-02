"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from transcription_service.api import health, transcribe, stream, sessions
from transcription_service.config import get_settings
from transcription_service.core.models import init_models, get_models
from transcription_service.core.session_manager import SessionManager, SessionManagerConfig
from transcription_service.dependencies import set_session_manager, clear_session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - load models and start session manager."""
    # Startup: load shared models
    config = get_settings()
    init_models(config)

    # Initialize session manager
    models = get_models()
    manager_config = SessionManagerConfig(
        max_sessions=config.max_sessions,
        idle_timeout_seconds=config.session_idle_timeout_seconds,
        cleanup_interval_seconds=config.session_cleanup_interval_seconds,
    )
    session_manager = SessionManager(models, config, manager_config)
    await session_manager.start()
    set_session_manager(session_manager)

    yield

    # Shutdown: stop session manager (use the same instance from startup)
    await session_manager.stop()
    clear_session_manager()


app = FastAPI(
    title="Transcription Service",
    description="Scalable transcription service backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(health.router)
app.include_router(transcribe.router)
app.include_router(stream.router)
app.include_router(sessions.router)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "transcription_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )

"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from transcription_service.api import health, transcribe, stream
from transcription_service.config import get_settings
from transcription_service.core.models import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler - load models at startup."""
    # Startup: load shared models
    config = get_settings()
    init_models(config)

    yield

    # Shutdown: cleanup if needed (models will be garbage collected)


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

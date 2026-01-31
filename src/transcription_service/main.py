"""FastAPI application entry point."""

from fastapi import FastAPI

from transcription_service.api import health, transcribe, stream

app = FastAPI(
    title="Transcription Service",
    description="Scalable transcription service backend",
    version="0.1.0",
)

# Register routers
app.include_router(health.router)
app.include_router(transcribe.router)
app.include_router(stream.router)

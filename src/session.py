from enum import Enum
from typing import Optional, List, Dict
from datetime import datetime
import asyncio
import logging

from src.audio_processor import AudioProcessor
from src.endpointing import Endpointing

logger = logging.getLogger(__name__)


class SessionState(Enum):
    INIT = "init"
    STREAMING = "streaming"
    FINALIZING = "finalizing"
    CLOSED = "closed"


class TranscriptionSession:
    def __init__(self, session_id: str, asr_engine=None, config=None):
        self.session_id = session_id
        self.state = SessionState.INIT
        self.created_at = datetime.utcnow()
        self._lock = asyncio.Lock()

        # ASR components (optional for backward compatibility)
        self.asr_engine = asr_engine
        self.config = config

        if asr_engine and config:
            # Initialize real ASR components
            self.audio_processor = AudioProcessor(config.audio)
            self.endpointing = Endpointing(config.endpointing)
        else:
            # Fallback for testing without ASR
            self.audio_processor = None
            self.endpointing = None

        # Legacy buffer (kept for backward compatibility)
        self.audio_buffer = bytearray()

        # Transcription state
        self.current_partial = ""
        self.final_transcripts = []
        self.transcript_parts = []  # Legacy field

    async def start_streaming(self):
        async with self._lock:
            if self.state != SessionState.INIT:
                raise ValueError(f"Cannot start streaming from state {self.state}")
            self.state = SessionState.STREAMING
            logger.info(f"Session {self.session_id}: INIT -> STREAMING")

            # Reset endpointing on start
            if self.endpointing:
                self.endpointing.reset()

    async def add_audio_chunk(self, audio_data: bytes) -> List[Dict]:
        """
        Process audio chunk and return transcription results.

        Args:
            audio_data: Raw PCM audio bytes

        Returns:
            List of result dictionaries with type, text, is_partial fields
        """
        async with self._lock:
            if self.state != SessionState.STREAMING:
                raise ValueError(f"Cannot add audio in state {self.state}")

            # Legacy behavior - update buffer
            self.audio_buffer.extend(audio_data)

            # If no ASR components, return empty results
            if not self.audio_processor or not self.asr_engine:
                return []

            results = []

            try:
                # Add audio to processor
                self.audio_processor.add_audio(audio_data)

                # Get chunks ready for inference
                chunks = self.audio_processor.get_inference_chunks()

                for chunk in chunks:
                    # Transcribe chunk
                    transcript_result = await self.asr_engine.transcribe_chunk(chunk)

                    # Check for endpoint
                    is_endpoint = self.endpointing.process_audio(chunk)

                    if is_endpoint:
                        # Finalize current utterance
                        if self.current_partial:
                            self.final_transcripts.append(self.current_partial)
                            results.append({
                                "type": "final_transcript",
                                "text": self.current_partial,
                                "is_partial": False
                            })
                            logger.debug(
                                f"Finalized transcript: {self.current_partial[:50]}..."
                            )

                        # Start new utterance
                        self.current_partial = transcript_result["text"]
                        self.endpointing.reset()

                    else:
                        # Update partial transcript
                        self.current_partial = transcript_result["text"]
                        results.append({
                            "type": "partial_transcript",
                            "text": transcript_result["text"],
                            "is_partial": True
                        })

            except Exception as e:
                logger.error(f"Error processing audio chunk: {e}", exc_info=True)
                raise

            return results

    async def finalize(self):
        """Finalize the session and flush any remaining audio"""
        async with self._lock:
            if self.state == SessionState.CLOSED:
                return

            if self.state == SessionState.STREAMING:
                self.state = SessionState.FINALIZING
                logger.info(f"Session {self.session_id}: STREAMING -> FINALIZING")

                # Flush any remaining audio
                if self.audio_processor:
                    remaining_audio = self.audio_processor.flush()
                    if remaining_audio is not None and len(remaining_audio) > 0:
                        try:
                            # Transcribe remaining audio
                            result = await self.asr_engine.transcribe_chunk(remaining_audio)
                            if result["text"]:
                                self.current_partial = result["text"]
                        except Exception as e:
                            logger.warning(f"Error transcribing remaining audio: {e}")

                # Finalize current partial if exists
                if self.current_partial:
                    self.final_transcripts.append(self.current_partial)
                    logger.info(f"Final transcript: {self.current_partial}")

    async def close(self):
        """Close the session and clean up resources"""
        async with self._lock:
            previous_state = self.state
            self.state = SessionState.CLOSED

            # Clean up buffers
            self.audio_buffer.clear()
            if self.audio_processor:
                self.audio_processor.reset()

            logger.info(f"Session {self.session_id}: {previous_state} -> CLOSED")

    def get_state(self) -> SessionState:
        return self.state

    def get_final_transcript(self) -> str:
        """Get the complete final transcript"""
        return " ".join(self.final_transcripts)

    def get_stats(self) -> Dict:
        """Get session statistics"""
        stats = {
            "session_id": self.session_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "final_transcripts_count": len(self.final_transcripts),
            "has_asr": self.asr_engine is not None
        }

        if self.audio_processor:
            stats["audio_stats"] = self.audio_processor.get_stats()

        if self.endpointing:
            stats["endpointing_stats"] = self.endpointing.get_stats()

        return stats


class SessionManager:
    def __init__(self, asr_engine=None, config=None):
        self.sessions: dict[str, TranscriptionSession] = {}
        self.asr_engine = asr_engine
        self.config = config
        self._lock = asyncio.Lock()

    async def create_session(self, session_id: str) -> TranscriptionSession:
        async with self._lock:
            if session_id in self.sessions:
                logger.warning(f"Session {session_id} already exists, closing old session")
                await self.sessions[session_id].close()

            # Create session with ASR components
            session = TranscriptionSession(
                session_id=session_id,
                asr_engine=self.asr_engine,
                config=self.config
            )
            self.sessions[session_id] = session
            logger.info(f"Created session {session_id}")
            return session

    async def get_session(self, session_id: str) -> Optional[TranscriptionSession]:
        async with self._lock:
            return self.sessions.get(session_id)

    async def close_session(self, session_id: str):
        async with self._lock:
            if session_id in self.sessions:
                await self.sessions[session_id].close()
                del self.sessions[session_id]
                logger.info(f"Removed session {session_id}")

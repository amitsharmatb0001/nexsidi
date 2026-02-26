"""Voice-to-Text Service: transcribes audio from WhatsApp and file uploads.

Supports multiple input formats and languages:
- Audio formats: OGG (WhatsApp), MP3, WAV, WebM, M4A
- Languages: English, Hindi, regional Indian languages (auto-detected)
- Max duration: 10 minutes per clip (WhatsApp limit: 16 MB)

Pipeline:
1. Validate audio file (format, size, duration estimate)
2. Convert to WAV if needed (production: ffmpeg)
3. Send to speech-to-text provider (production: Google Speech-to-Text / Whisper)
4. Return transcription with confidence score + detected language

Security (DPDP):
- Audio content is NOT stored after transcription
- Transcription text is stored only in pipeline context (encrypted at rest)
- No PII extracted from audio without user consent
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AudioFormat(str, Enum):
    """Supported audio formats."""

    OGG = "ogg"         # WhatsApp voice notes
    MP3 = "mp3"
    WAV = "wav"
    WEBM = "webm"
    M4A = "m4a"
    MP4 = "mp4"         # Audio track from video


class TranscriptionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TOO_LONG = "too_long"
    UNSUPPORTED_FORMAT = "unsupported_format"


# MIME type to AudioFormat mapping
MIME_TO_FORMAT: dict[str, AudioFormat] = {
    "audio/ogg": AudioFormat.OGG,
    "audio/opus": AudioFormat.OGG,          # WhatsApp sends opus in ogg container
    "audio/mpeg": AudioFormat.MP3,
    "audio/mp3": AudioFormat.MP3,
    "audio/wav": AudioFormat.WAV,
    "audio/x-wav": AudioFormat.WAV,
    "audio/webm": AudioFormat.WEBM,
    "audio/mp4": AudioFormat.MP4,
    "audio/x-m4a": AudioFormat.M4A,
    "audio/m4a": AudioFormat.M4A,
}

# Supported languages with BCP-47 codes
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "gu": "Gujarati",
    "bn": "Bengali",
    "pa": "Punjabi",
}

# Limits
MAX_AUDIO_BYTES = 25 * 1024 * 1024     # 25 MB
MAX_DURATION_SECONDS = 600               # 10 minutes
BYTES_PER_SECOND_ESTIMATE = 16_000       # Rough estimate for bitrate-based duration


@dataclass(slots=True)
class TranscriptionResult:
    """Result of a voice-to-text transcription."""

    status: TranscriptionStatus
    text: str = ""
    language_code: str = ""
    language_name: str = ""
    confidence: float = 0.0
    duration_seconds: float = 0.0
    word_count: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "text": self.text[:2000],
            "language_code": self.language_code,
            "language_name": self.language_name,
            "confidence": round(self.confidence, 3),
            "duration_seconds": round(self.duration_seconds, 1),
            "word_count": self.word_count,
            "error": self.error,
        }


class VoiceToTextService:
    """Transcribes audio content to text.

    In production, integrates with:
    - Google Cloud Speech-to-Text (primary, best Hindi support)
    - OpenAI Whisper (fallback, self-hosted for cost)

    Currently: returns structured placeholders for pipeline integration.
    Audio validation and format detection are fully functional.
    """

    def __init__(self) -> None:
        self._transcription_count: int = 0

    def validate_audio(
        self,
        content: bytes,
        mime_type: str,
        filename: str = "",
    ) -> TranscriptionResult | None:
        """Validate audio file before transcription.

        Returns None if valid, or a TranscriptionResult with error if invalid.
        """
        # Check format
        audio_format = self.detect_format(mime_type, filename)
        if audio_format is None:
            return TranscriptionResult(
                status=TranscriptionStatus.UNSUPPORTED_FORMAT,
                error=f"Unsupported audio format: {mime_type}",
            )

        # Check size
        if len(content) > MAX_AUDIO_BYTES:
            return TranscriptionResult(
                status=TranscriptionStatus.FAILED,
                error=f"Audio file too large: {len(content)} bytes (max {MAX_AUDIO_BYTES})",
            )

        # Estimate duration from file size
        estimated_duration = len(content) / BYTES_PER_SECOND_ESTIMATE
        if estimated_duration > MAX_DURATION_SECONDS:
            return TranscriptionResult(
                status=TranscriptionStatus.TOO_LONG,
                error=f"Audio too long: ~{estimated_duration:.0f}s (max {MAX_DURATION_SECONDS}s)",
                duration_seconds=estimated_duration,
            )

        return None  # Valid

    def detect_format(self, mime_type: str, filename: str = "") -> AudioFormat | None:
        """Detect audio format from MIME type or filename extension."""
        # Try MIME type first
        fmt = MIME_TO_FORMAT.get(mime_type)
        if fmt is not None:
            return fmt

        # Fallback to extension
        if filename and "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            ext_map: dict[str, AudioFormat] = {
                "ogg": AudioFormat.OGG,
                "opus": AudioFormat.OGG,
                "mp3": AudioFormat.MP3,
                "wav": AudioFormat.WAV,
                "webm": AudioFormat.WEBM,
                "m4a": AudioFormat.M4A,
                "mp4": AudioFormat.MP4,
            }
            return ext_map.get(ext)

        return None

    async def transcribe(
        self,
        content: bytes,
        mime_type: str,
        filename: str = "",
        language_hint: str = "",
    ) -> TranscriptionResult:
        """Transcribe audio content to text.

        Args:
            content: Raw audio bytes.
            mime_type: MIME type of the audio.
            filename: Original filename (for format detection fallback).
            language_hint: BCP-47 language code hint (e.g., "hi" for Hindi).

        Returns:
            TranscriptionResult with text and metadata.
        """
        # Validate first
        validation_error = self.validate_audio(content, mime_type, filename)
        if validation_error is not None:
            return validation_error

        audio_format = self.detect_format(mime_type, filename)
        estimated_duration = len(content) / BYTES_PER_SECOND_ESTIMATE

        # In production: send to Google Speech-to-Text or Whisper
        # For now: structured placeholder that the pipeline can process
        language_code = language_hint if language_hint in SUPPORTED_LANGUAGES else "en"
        language_name = SUPPORTED_LANGUAGES.get(language_code, "English")

        self._transcription_count += 1

        logger.info(
            "voice_transcription",
            format=audio_format.value if audio_format else "unknown",
            size_bytes=len(content),
            estimated_duration=round(estimated_duration, 1),
            language_hint=language_hint,
        )

        return TranscriptionResult(
            status=TranscriptionStatus.COMPLETED,
            text=f"[Transcription from {filename or 'audio'} ({language_name})]",
            language_code=language_code,
            language_name=language_name,
            confidence=0.95,
            duration_seconds=estimated_duration,
            word_count=0,
        )

    async def transcribe_whatsapp_voice(
        self,
        media_content: bytes,
        phone_hash: str,
    ) -> TranscriptionResult:
        """Transcribe a WhatsApp voice note.

        WhatsApp voice notes are OGG/Opus format.
        Phone hash is for audit logging (never the actual number).
        """
        result = await self.transcribe(
            content=media_content,
            mime_type="audio/ogg",
            filename="voice_note.ogg",
        )

        logger.info(
            "whatsapp_voice_transcribed",
            phone_hash=phone_hash[:8],
            status=result.status.value,
            duration=round(result.duration_seconds, 1),
        )

        return result

    @property
    def transcription_count(self) -> int:
        """Total transcriptions processed."""
        return self._transcription_count


# ── Singleton ───────────────────────────────────────────────────

_service: VoiceToTextService | None = None


def get_voice_to_text_service() -> VoiceToTextService:
    """Get or create the voice-to-text service singleton."""
    global _service
    if _service is None:
        _service = VoiceToTextService()
    return _service

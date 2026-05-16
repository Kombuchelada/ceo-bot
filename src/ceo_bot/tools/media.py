"""Optional post-archive enrichment: OCR for images, transcription for audio.

Runs out-of-band so message archiving is never blocked by Anthropic API latency.
Hook this in from handlers/messages.py once we want it live.
"""

from __future__ import annotations

from typing import Any

from ceo_bot import storage
from ceo_bot.config import settings


async def maybe_ocr_image(attachment_id: int, s3_key: str, content_type: str) -> str | None:
    if not settings.enable_media_ocr:
        return None
    if not content_type.startswith("image/"):
        return None
    _ = storage.get_object(s3_key)  # noqa: F841
    # TODO: hand image bytes to Anthropic vision via claude.py with an OCR prompt,
    # then UPDATE attachments SET ocr_text = ? WHERE id = ?.
    return None


async def maybe_transcribe_audio(
    attachment_id: int, s3_key: str, content_type: str
) -> dict[str, Any] | None:
    if not settings.enable_audio_transcription:
        return None
    if not content_type.startswith(("audio/", "video/")):
        return None
    # TODO: pipe through ffmpeg -> whisper.cpp or an API transcription service,
    # then UPDATE attachments SET transcript = ? WHERE id = ?.
    return None

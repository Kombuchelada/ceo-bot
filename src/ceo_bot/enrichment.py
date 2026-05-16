"""Background enrichment of message attachments: extract text and a short
description for images, frame-level descriptions for videos. Results land in
attachments.ocr_text / .transcript / .summary and are indexed into the
attachments_fts FTS5 table so the search_history tool can match against them.
"""

from __future__ import annotations

import asyncio
import base64
import io
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pillow_heif
import structlog
from anthropic import AsyncAnthropic
from PIL import Image

from ceo_bot.config import settings
from ceo_bot.db import cursor

log = structlog.get_logger()

pillow_heif.register_heif_opener()

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# Anthropic vision recommends ~1568px max edge and ≤5MB per image.
MAX_EDGE_PX = 1568
JPEG_QUALITY = 85
MAX_BYTES = 5 * 1024 * 1024
VIDEO_FRAMES = 5

IMAGE_PROMPT = """Look at this image carefully and reply with exactly two sections:

OCR:
Any text that's visibly written in the image, verbatim. Use "(none)" if none.

DESCRIPTION:
A factual 1-3 sentence description of what's depicted. Be specific (objects,
people, setting, action). Do not editorialize."""

VIDEO_PROMPT = """These are {n} frames sampled in order from a single video.
Reply with two sections:

OCR:
Any text visible across the frames, verbatim. Use "(none)" if none.

DESCRIPTION:
2-4 sentences describing what's happening across the video as a sequence."""


def _is_image(content_type: str | None) -> bool:
    return bool(content_type) and content_type.startswith("image/")


def _is_video(content_type: str | None) -> bool:
    return bool(content_type) and content_type.startswith("video/")


def _to_jpeg_bytes(data: bytes) -> bytes:
    """Decode (HEIC-aware via pillow_heif), downsize, re-encode as JPEG ≤MAX_BYTES."""
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)

    for quality in (JPEG_QUALITY, 70, 55, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) <= MAX_BYTES:
            return out
    return out


def _b64_image_block(jpeg_bytes: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(jpeg_bytes).decode(),
        },
    }


def _parse_sections(text: str) -> tuple[str, str]:
    """Split the model's "OCR:\\n...\\n\\nDESCRIPTION:\\n..." reply."""
    ocr, desc = "", ""
    parts = text.split("DESCRIPTION:", 1)
    if len(parts) == 2:
        ocr_part, desc = parts[0], parts[1].strip()
        ocr = ocr_part.replace("OCR:", "", 1).strip()
    else:
        desc = text.strip()
    if ocr.lower() in ("(none)", "none", ""):
        ocr = ""
    return ocr, desc


def _extract_video_frames(data: bytes) -> list[bytes]:
    """Use ffmpeg/ffprobe to grab VIDEO_FRAMES evenly-spaced JPEG frames."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as src_f:
        src_f.write(data)
        src_path = src_f.name
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                src_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(probe.stdout.strip() or "0")
        if duration <= 0:
            return []
        frames: list[bytes] = []
        for i in range(1, VIDEO_FRAMES + 1):
            ts = duration * i / (VIDEO_FRAMES + 1)
            with tempfile.NamedTemporaryFile(suffix=".jpg") as out_f:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{ts:.3f}",
                        "-i",
                        src_path,
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        out_f.name,
                    ],
                    check=True,
                )
                raw = Path(out_f.name).read_bytes()
                if raw:
                    frames.append(_to_jpeg_bytes(raw))
        return frames
    finally:
        Path(src_path).unlink(missing_ok=True)


async def _ask_claude(content_blocks: list[dict[str, Any]]) -> str:
    resp = await _client.messages.create(
        model=settings.anthropic_model,
        max_tokens=512,
        messages=[{"role": "user", "content": content_blocks}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def _write_result(attachment_id: int, ocr: str, summary: str, transcript: str = "") -> None:
    body = " ".join(filter(None, [ocr, summary, transcript])).strip()
    with cursor() as cur:
        cur.execute(
            "UPDATE attachments SET ocr_text=?, summary=?, transcript=? WHERE id=?",
            (ocr or None, summary or None, transcript or None, attachment_id),
        )
        # Refresh FTS row (contentless table)
        cur.execute("DELETE FROM attachments_fts WHERE rowid=?", (attachment_id,))
        if body:
            cur.execute(
                "INSERT INTO attachments_fts(rowid, body) VALUES (?, ?)",
                (attachment_id, body),
            )


async def enrich_attachment(
    attachment_id: int, content_type: str | None, data: bytes
) -> None:
    try:
        if _is_image(content_type):
            jpeg = await asyncio.to_thread(_to_jpeg_bytes, data)
            reply = await _ask_claude([_b64_image_block(jpeg), {"type": "text", "text": IMAGE_PROMPT}])
            ocr, summary = _parse_sections(reply)
            await asyncio.to_thread(_write_result, attachment_id, ocr, summary)
            log.info("enrichment.image.done", attachment_id=attachment_id, ocr_chars=len(ocr), desc_chars=len(summary))
        elif _is_video(content_type):
            frames = await asyncio.to_thread(_extract_video_frames, data)
            if not frames:
                log.warning("enrichment.video.no_frames", attachment_id=attachment_id)
                return
            blocks: list[dict[str, Any]] = [_b64_image_block(f) for f in frames]
            blocks.append({"type": "text", "text": VIDEO_PROMPT.format(n=len(frames))})
            reply = await _ask_claude(blocks)
            ocr, desc = _parse_sections(reply)
            # For videos the cross-frame narrative is the transcript; description is the summary.
            await asyncio.to_thread(_write_result, attachment_id, ocr, desc, desc)
            log.info("enrichment.video.done", attachment_id=attachment_id, frames=len(frames))
        else:
            log.info("enrichment.skipped", attachment_id=attachment_id, content_type=content_type)
    except Exception:
        log.exception("enrichment.failed", attachment_id=attachment_id, content_type=content_type)

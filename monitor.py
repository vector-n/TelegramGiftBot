"""
monitor.py — Watches source channels via Telethon and feeds content into the queue.

Media handling:
  ✅  Photos, videos, GIFs, regular stickers, documents, voice notes
  ❌  Premium video stickers (webm, requires Telegram Premium) — silently skipped
  ❌  Files over MAX_MEDIA_MB — skipped with a log warning
  ❌  Custom emoji (premium inline emoji in text) — stripped from text before processing
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from telethon import events
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    DocumentAttributeAnimated,
    DocumentAttributeSticker,
    DocumentAttributeCustomEmoji,
    DocumentAttributeAudio,
)

import client as telethon_client
import config
from ai import translate_and_rewrite
from database import (
    is_seen, mark_seen, enqueue, update_media_path, bump_stats, get_setting,
)

logger = logging.getLogger(__name__)

# Will be set by main.py so monitor can notify admin via the bot
_bot = None
_admin_id: int = config.ADMIN_ID


def set_bot(bot) -> None:
    """Inject the python-telegram-bot Bot instance for admin notifications."""
    global _bot
    _bot = bot


# ─────────────────────────────────────────────────────────────────────────────
#  MIME TYPE → FILE EXTENSION MAP
# ─────────────────────────────────────────────────────────────────────────────

_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg":              "jpg",
    "image/png":               "png",
    "image/webp":              "webp",
    "image/gif":               "gif",
    "video/mp4":               "mp4",
    "video/webm":              "webm",
    "audio/ogg":               "ogg",
    "audio/mpeg":              "mp3",
    "application/x-tgsticker": "tgs",   # Lottie animated sticker
}


# ─────────────────────────────────────────────────────────────────────────────
#  MEDIA CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def classify_media(message) -> tuple[Optional[str], bool]:
    """
    Returns (media_type, can_download).

    media_type values:
      'photo' | 'video' | 'video_note' | 'animation' | 'sticker' | 'audio' | 'document' | None
      'skip'  — media exists but is premium/oversized and must be ignored

    can_download:
      True  — we should try to download this media
      False — either no media, or media is 'skip'
    """
    if not message.media:
        return None, False

    # ── Photo ──────────────────────────────────────────────────────────────
    if isinstance(message.media, MessageMediaPhoto):
        return "photo", True

    # ── Document (covers stickers, gifs, videos, voice, files, ...) ───────
    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        attr_map = {type(a): a for a in doc.attributes}

        # 🚫 Premium custom emoji (inline) — not a standalone media message,
        #    just decorative text; silently ignore the media portion.
        if DocumentAttributeCustomEmoji in attr_map:
            return "skip", False

        # 🚫 Premium video sticker (webm format) — bots on free accounts
        #    cannot forward these; skip to avoid send failures.
        if DocumentAttributeSticker in attr_map and doc.mime_type == "video/webm":
            logger.debug("Skipping premium video sticker (webm).")
            return "skip", False

        # 🚫 File too large for the Bot API (50 MB hard limit)
        if doc.size > config.MAX_MEDIA_BYTES:
            logger.info(
                f"Skipping oversized media "
                f"({doc.size // 1_048_576} MB > {config.MAX_MEDIA_MB} MB limit)."
            )
            return "skip", False

        # ✅ Regular / Lottie sticker
        if DocumentAttributeSticker in attr_map:
            return "sticker", True

        # ✅ Video (includes round video notes)
        if DocumentAttributeVideo in attr_map:
            v_attr = attr_map[DocumentAttributeVideo]
            if getattr(v_attr, "round_message", False):
                return "video_note", True
            return "video", True

        # ✅ Animated GIF (Telegram stores GIFs as MPEG4)
        if DocumentAttributeAnimated in attr_map:
            return "animation", True

        # ✅ Audio / Voice
        if DocumentAttributeAudio in attr_map:
            a_attr = attr_map[DocumentAttributeAudio]
            return "voice" if getattr(a_attr, "voice", False) else "audio", True

        # ✅ Generic document / file
        return "document", True

    return None, False


# ─────────────────────────────────────────────────────────────────────────────
#  MEDIA DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

def _extension_for(message, media_type: str) -> str:
    """Determine the correct file extension for a downloaded media file."""
    if media_type == "photo":
        return "jpg"
    if isinstance(message.media, MessageMediaDocument):
        mime = message.media.document.mime_type
        return _MIME_TO_EXT.get(mime, "bin")
    return "bin"


async def download_media(message, media_type: str, queue_id: int) -> Optional[str]:
    """
    Download media via Telethon to MEDIA_DIR/{queue_id}.{ext}.
    Returns the final file path, or None if download failed.
    """
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = _extension_for(message, media_type)
    final_path = config.MEDIA_DIR / f"{queue_id}.{ext}"

    try:
        cl = telethon_client.get()
        await cl.download_media(message.media, file=str(final_path))
        logger.info(f"📥 Downloaded {media_type} → {final_path} ({final_path.stat().st_size // 1024} KB)")
        return str(final_path)
    except Exception as e:
        logger.error(f"❌ Media download failed for queue #{queue_id}: {e}")
        if final_path.exists():
            final_path.unlink()
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  TEXT CLEANING
# ─────────────────────────────────────────────────────────────────────────────

def _strip_premium_emoji(text: str) -> str:
    """
    Telegram premium custom emoji appear as Unicode private-use area characters.
    Strip them so the AI doesn't process garbage characters.
    """
    # Remove Unicode private use area blocks used for custom emoji
    return re.sub(r"[\U000E0000-\U000E01FF\uFE00-\uFE0F]", "", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
#  MESSAGE PROCESSOR
# ─────────────────────────────────────────────────────────────────────────────

async def process_message(event) -> None:
    """Core handler: classify → deduplicate → AI → queue → notify."""
    try:
        message = event.message
        source_channel = str(event.chat_id)

        # ── Deduplication ──────────────────────────────────────────────────
        if await is_seen(source_channel, message.id):
            return
        await mark_seen(source_channel, message.id)
        await bump_stats(seen=1)

        # ── Classify media ─────────────────────────────────────────────────
        media_type, can_download = classify_media(message)

        # ── Extract text ───────────────────────────────────────────────────
        raw_text = (message.text or message.caption or "").strip()
        text = _strip_premium_emoji(raw_text)

        # Skip messages with no usable content
        if not text and (not media_type or media_type == "skip"):
            logger.debug(f"Skipping empty/ineligible msg {message.id} from {source_channel}")
            return

        if media_type == "skip" and not text:
            return

        logger.info(
            f"📨  msg {message.id} | src: {source_channel} "
            f"| media: {media_type} | text: {len(text)} chars"
        )

        # ── AI processing ──────────────────────────────────────────────────
        arabic = await translate_and_rewrite(text, has_media=bool(media_type and media_type != "skip"))
        if not arabic:
            logger.warning(f"⚠️ AI returned nothing for msg {message.id} — skipping.")
            return
        await bump_stats(processed=1)

        # ── Determine queue status ─────────────────────────────────────────
        require_approval = (await get_setting("require_approval", "false")) == "true"
        status = "pending" if require_approval else "approved"

        # ── Add to queue (without media_path yet) ──────────────────────────
        queue_id = await enqueue(
            arabic_text=arabic,
            original_text=text,
            media_path=None,           # will be updated after download
            media_type=media_type if media_type != "skip" else None,
            source_channel=source_channel,
            source_message_id=message.id,
            status=status,
        )

        # ── Download media (now we have the queue_id for the filename) ─────
        if media_type and media_type != "skip" and can_download:
            media_path = await download_media(message, media_type, queue_id)
            if media_path:
                await update_media_path(queue_id, media_path)

        logger.info(f"✅ Queued post #{queue_id}  status={status}  media={media_type}")

        # ── Notify admin if approval mode is on ───────────────────────────
        if require_approval and _bot and _admin_id:
            preview = arabic[:300] + ("…" if len(arabic) > 300 else "")
            media_label = f" | 🖼 {media_type}" if media_type and media_type != "skip" else ""
            try:
                await _bot.send_message(
                    chat_id=_admin_id,
                    text=(
                        f"📬 <b>منشور جديد بانتظار موافقتك</b>{media_label}\n\n"
                        f"<b>المصدر:</b> <code>{source_channel}</code>\n\n"
                        f"<b>المعاينة:</b>\n{preview}\n\n"
                        f"للموافقة: <code>/approve {queue_id}</code>\n"
                        f"للرفض:    <code>/reject {queue_id}</code>\n"
                        f"للمعاينة: <code>/preview {queue_id}</code>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as notify_err:
                logger.warning(f"Could not notify admin: {notify_err}")

    except Exception as e:
        logger.error(f"❌ process_message error: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
#  START / STOP
# ─────────────────────────────────────────────────────────────────────────────

async def start_monitor() -> None:
    """Register event handlers and run until the Telethon client disconnects."""
    sources = config.source_channels()

    if not sources:
        logger.warning("⚠️ No SOURCE_CHANNELS set — monitor is idle.")
        # Keep this coroutine alive so the task doesn't die immediately
        import asyncio
        await asyncio.Event().wait()
        return

    cl = telethon_client.get()
    logger.info(f"👀 Monitoring {len(sources)} channel(s): {sources}")

    @cl.on(events.NewMessage(chats=sources))
    async def _handler(event):
        await process_message(event)

    # Blocks until client.stop() is called
    await cl.run_until_disconnected()

"""
poster.py — Sends approved posts from the queue to all target channels.

Supports: text, photo, video, animation (GIF), sticker, voice, audio,
          video_note (round video), and generic documents.

After a successful post the local media file is deleted to save disk space.
"""

import logging
import time
from pathlib import Path
from typing import Optional

from telegram import Bot, InputFile
from telegram.error import TelegramError

import config
from database import get_next_approved, update_status, log_post, bump_stats, get_setting

logger = logging.getLogger(__name__)

# In-memory timestamp of the last successful post (seconds since epoch)
_last_post_time: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def reset_delay() -> None:
    """Call this before /postnow to bypass the inter-post delay check."""
    global _last_post_time
    _last_post_time = 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  SEND A SINGLE POST TO ONE CHANNEL
# ─────────────────────────────────────────────────────────────────────────────

async def _send_to_channel(
    bot: Bot,
    channel: str,
    text: str,
    media_path: Optional[str],
    media_type: Optional[str],
) -> Optional[int]:
    """
    Send one post to one channel.
    Returns the Telegram message_id on success, or None on failure.
    """
    parse_mode = "HTML"

    try:
        # ── Text only ──────────────────────────────────────────────────────
        if not media_path or not Path(media_path).exists():
            msg = await bot.send_message(
                chat_id=channel, text=text, parse_mode=parse_mode
            )
            return msg.message_id

        # ── Media + caption ────────────────────────────────────────────────
        with open(media_path, "rb") as f:
            file = InputFile(f)
            caption = text  # use the Arabic text as caption

            match media_type:
                case "photo":
                    msg = await bot.send_photo(
                        chat_id=channel, photo=file,
                        caption=caption, parse_mode=parse_mode,
                    )
                case "video":
                    msg = await bot.send_video(
                        chat_id=channel, video=file,
                        caption=caption, parse_mode=parse_mode,
                        supports_streaming=True,
                    )
                case "animation":
                    msg = await bot.send_animation(
                        chat_id=channel, animation=file,
                        caption=caption, parse_mode=parse_mode,
                    )
                case "sticker":
                    # Stickers don't support captions — send sticker then text
                    await bot.send_sticker(chat_id=channel, sticker=file)
                    msg = await bot.send_message(
                        chat_id=channel, text=text, parse_mode=parse_mode
                    )
                case "video_note":
                    # Round videos don't support captions either
                    await bot.send_video_note(chat_id=channel, video_note=file)
                    msg = await bot.send_message(
                        chat_id=channel, text=text, parse_mode=parse_mode
                    )
                case "voice":
                    msg = await bot.send_voice(
                        chat_id=channel, voice=file,
                        caption=caption, parse_mode=parse_mode,
                    )
                case "audio":
                    msg = await bot.send_audio(
                        chat_id=channel, audio=file,
                        caption=caption, parse_mode=parse_mode,
                    )
                case _:  # document / fallback
                    msg = await bot.send_document(
                        chat_id=channel, document=file,
                        caption=caption, parse_mode=parse_mode,
                    )

        return msg.message_id

    except TelegramError as e:
        logger.error(f"❌ Telegram error posting to {channel}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error posting to {channel}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  POST NEXT IN QUEUE
# ─────────────────────────────────────────────────────────────────────────────

async def post_next_in_queue(bot: Bot, skip_delay: bool = False) -> bool:
    """
    Grab the oldest approved post, send it to all target channels, and
    mark it as posted.  Returns True if a post was sent.
    """
    global _last_post_time

    # ── Auto-post enabled? ─────────────────────────────────────────────────
    auto_post = (await get_setting("auto_post", "true")) == "true"
    if not auto_post and not skip_delay:
        logger.debug("Auto-post is disabled.")
        return False

    # ── Delay check ─────────────────────────────────────────────────────────
    if not skip_delay:
        delay_minutes = int(await get_setting("post_delay_minutes", "30"))
        elapsed = time.monotonic() - _last_post_time
        if _last_post_time > 0 and elapsed < delay_minutes * 60:
            remaining = int(delay_minutes * 60 - elapsed)
            logger.debug(f"⏳ Next post in {remaining}s")
            return False

    # ── Fetch next post ────────────────────────────────────────────────────
    post = await get_next_approved()
    if not post:
        logger.debug("📭 Queue is empty.")
        return False

    queue_id   = post["id"]
    text       = post["arabic_text"]
    media_path = post.get("media_path")
    media_type = post.get("media_type")
    targets    = config.target_channels()

    if not targets:
        logger.error("❌ No TARGET_CHANNELS configured!")
        return False

    # ── Send to all channels ───────────────────────────────────────────────
    success_count = 0
    for channel in targets:
        msg_id = await _send_to_channel(bot, channel, text, media_path, media_type)
        if msg_id:
            await log_post(queue_id, channel, msg_id)
            success_count += 1
            logger.info(f"✅ Posted #{queue_id} to {channel} (msg {msg_id})")

    # ── Finalise ───────────────────────────────────────────────────────────
    if success_count > 0:
        await update_status(queue_id, "posted")
        await bump_stats(posts=1)
        _last_post_time = time.monotonic()

        # Delete local media file to free disk space
        if media_path:
            try:
                Path(media_path).unlink(missing_ok=True)
                logger.debug(f"🗑️ Deleted media file: {media_path}")
            except Exception:
                pass

        return True
    else:
        # Mark as failed so it doesn't keep blocking the queue
        await update_status(queue_id, "failed")
        logger.error(f"⚠️ Post #{queue_id} failed on all {len(targets)} channel(s) — marked as failed.")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  POST SPECIFIC ID  (for /postnow <id> command)
# ─────────────────────────────────────────────────────────────────────────────

async def post_specific(bot: Bot, queue_id: int) -> bool:
    """Approve and immediately post a specific queue item."""
    from database import get_post, update_status as db_update
    post = await get_post(queue_id)
    if not post:
        return False

    await db_update(queue_id, "approved")

    # Reload so we have the fresh status
    post = await get_post(queue_id)
    text       = post["arabic_text"]
    media_path = post.get("media_path")
    media_type = post.get("media_type")
    targets    = config.target_channels()

    if not targets:
        return False

    global _last_post_time
    success_count = 0
    for channel in targets:
        msg_id = await _send_to_channel(bot, channel, text, media_path, media_type)
        if msg_id:
            await log_post(queue_id, channel, msg_id)
            success_count += 1

    if success_count > 0:
        await db_update(queue_id, "posted")
        await bump_stats(posts=1)
        _last_post_time = time.monotonic()
        if media_path:
            Path(media_path).unlink(missing_ok=True)
        return True
    else:
        await db_update(queue_id, "failed")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  BROADCAST CUSTOM TEXT  (for /summary)
# ─────────────────────────────────────────────────────────────────────────────

async def broadcast(bot: Bot, text: str, channels: Optional[list] = None) -> int:
    """Send a custom text message to all (or given) target channels."""
    targets = channels or config.target_channels()
    sent = 0
    for channel in targets:
        try:
            await bot.send_message(chat_id=channel, text=text, parse_mode="HTML")
            sent += 1
            logger.info(f"📣 Broadcast → {channel}")
        except TelegramError as e:
            logger.error(f"❌ Broadcast to {channel} failed: {e}")
    return sent

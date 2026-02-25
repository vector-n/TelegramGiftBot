"""
client.py — Singleton Telethon user client.

Both monitor.py (for event subscriptions) and poster.py (for media downloads)
import this module to share the same connected session.
"""

import logging
from typing import Optional

from telethon import TelegramClient
import config

logger = logging.getLogger(__name__)

_client: Optional[TelegramClient] = None


def get() -> TelegramClient:
    """Return the shared client. Raises if not yet started."""
    if _client is None:
        raise RuntimeError("Telethon client not started. Call client.start() first.")
    return _client


async def start() -> None:
    """
    Connect and authenticate the Telethon user client.

    - If SESSION_STRING env var is set (Render / any server without persistent disk):
      uses a string session directly — no .session file needed, survives redeploys.
    - Otherwise falls back to a local .session file (normal local dev usage).
    """
    global _client
    import os
    session_string = os.getenv("SESSION_STRING", "").strip()

    if session_string:
        from telethon.sessions import StringSession
        session = StringSession(session_string)
        logger.info("🔑 Using SESSION_STRING from environment.")
    else:
        session = config.SESSION_NAME
        logger.info(f"🔑 Using local session file: {config.SESSION_NAME}.session")

    _client = TelegramClient(session, config.API_ID, config.API_HASH)
    await _client.start(phone=config.PHONE_NUMBER)
    me = await _client.get_me()
    logger.info(f"✅ Telethon signed in as: {me.first_name} (+{me.phone})")


async def stop() -> None:
    """Gracefully disconnect the client."""
    global _client
    if _client and _client.is_connected():
        await _client.disconnect()
        logger.info("🛑 Telethon client disconnected.")

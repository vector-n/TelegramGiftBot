"""
config.py — Loads .env and exposes typed configuration constants.
Call config.validate() once at startup to catch missing values early.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Required keys — bot refuses to start if any are missing ───────────────
_REQUIRED: dict[str, str] = {
    "BOT_TOKEN":       "Telegram Bot Token (from @BotFather)",
    "API_ID":          "Telegram API ID (from my.telegram.org/apps)",
    "API_HASH":        "Telegram API Hash (from my.telegram.org/apps)",
    "GROQ_API_KEY":    "Groq API Key (from console.groq.com — free)",
    "ADMIN_ID":        "Your Telegram User ID (send /start to @userinfobot)",
    "TARGET_CHANNELS": "Channel(s) to post to, e.g. @mychannel",
    "PHONE_NUMBER":    "Your phone number with country code, e.g. +966501234567",
}


def validate() -> None:
    """Exit with a helpful message if any required env var is missing."""
    missing = [
        f"  ❌  {key}  —  {desc}"
        for key, desc in _REQUIRED.items()
        if not os.getenv(key, "").strip() or os.getenv(key, "").startswith("your_")
    ]
    if missing:
        print("\n🚨  Missing required values in .env:\n")
        print("\n".join(missing))
        print("\n👉  Copy .env.example → .env and fill every field.\n")
        sys.exit(1)


# ── Typed constants (read once at import time) ────────────────────────────
BOT_TOKEN:    str  = os.getenv("BOT_TOKEN", "")
API_ID:       int  = int(os.getenv("API_ID", "0") or "0")
API_HASH:     str  = os.getenv("API_HASH", "")
GROQ_API_KEY: str  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL:   str  = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
ADMIN_ID:     int  = int(os.getenv("ADMIN_ID", "0") or "0")
PHONE_NUMBER: str  = os.getenv("PHONE_NUMBER", "")
TIMEZONE:     str  = os.getenv("TIMEZONE", "Asia/Riyadh")
SUMMARY_HOUR: int  = int(os.getenv("SUMMARY_HOUR", "20") or "20")
MAX_MEDIA_MB: int  = int(os.getenv("MAX_MEDIA_MB", "50") or "50")
DB_PATH:      str  = os.getenv("DB_PATH", "bot_data.db")
SESSION_NAME: str  = os.getenv("SESSION_NAME", "user_session")
MEDIA_DIR:    Path = Path(os.getenv("MEDIA_DIR", "media_cache"))


def target_channels() -> list[str]:
    raw = os.getenv("TARGET_CHANNELS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def source_channels() -> list[str]:
    raw = os.getenv("SOURCE_CHANNELS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


# Derived constant
MAX_MEDIA_BYTES: int = MAX_MEDIA_MB * 1024 * 1024

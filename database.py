"""
database.py — All async SQLite operations via aiosqlite.

Tables:
  seen_messages  — dedup tracker for source channel messages
  post_queue     — content waiting to be posted
  post_log       — audit log of every sent post
  settings       — persistent key/value config (survives restarts)
  stats_daily    — per-day counters
"""

import logging
from datetime import datetime
from typing import Optional

import aiosqlite
import config

logger = logging.getLogger(__name__)
DB = config.DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
#  INIT
# ─────────────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables and indexes on first run."""
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
            PRAGMA journal_mode = WAL;

            -- Tracks messages already fetched from source channels (dedup)
            CREATE TABLE IF NOT EXISTS seen_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_channel  TEXT    NOT NULL,
                message_id      INTEGER NOT NULL,
                seen_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_channel, message_id)
            );

            -- Content queue
            CREATE TABLE IF NOT EXISTS post_queue (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                original_text     TEXT,
                arabic_text       TEXT    NOT NULL,
                media_path        TEXT,
                media_type        TEXT,   -- photo | video | animation | sticker | document | video_note
                source_channel    TEXT,
                source_message_id INTEGER,
                status            TEXT    DEFAULT 'pending',
                                          -- pending | approved | rejected | posted | failed
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                posted_at         TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_queue_status  ON post_queue(status);
            CREATE INDEX IF NOT EXISTS idx_queue_created ON post_queue(created_at);

            -- Audit log: every successfully sent post
            CREATE TABLE IF NOT EXISTS post_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id            INTEGER,
                target_channel      TEXT,
                telegram_message_id INTEGER,
                posted_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Persistent settings (survives bot restarts unlike os.environ)
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Daily counters
            CREATE TABLE IF NOT EXISTS stats_daily (
                date           TEXT PRIMARY KEY,
                posts_sent     INTEGER DEFAULT 0,
                msgs_seen      INTEGER DEFAULT 0,
                msgs_processed INTEGER DEFAULT 0
            );
        """)
        await db.commit()
    logger.info("✅ Database ready.")


# ─────────────────────────────────────────────────────────────────────────────
#  SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = CURRENT_TIMESTAMP""",
            (key, value),
        )
        await db.commit()


async def init_settings() -> None:
    """Write .env defaults into settings table only if not already set."""
    import os
    defaults = {
        "auto_post":          os.getenv("AUTO_POST", "true").lower(),
        "require_approval":   os.getenv("REQUIRE_APPROVAL", "false").lower(),
        "post_delay_minutes": os.getenv("POST_DELAY_MINUTES", "30"),
    }
    async with aiosqlite.connect(DB) as db:
        for key, value in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  SEEN MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

async def is_seen(source_channel: str, message_id: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT 1 FROM seen_messages WHERE source_channel = ? AND message_id = ?",
            (source_channel, message_id),
        )
        return await cur.fetchone() is not None


async def mark_seen(source_channel: str, message_id: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO seen_messages (source_channel, message_id) VALUES (?, ?)",
            (source_channel, message_id),
        )
        await db.commit()


async def cleanup_seen(days: int = 30) -> int:
    """Delete seen_messages entries older than `days` days. Returns deleted count."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "DELETE FROM seen_messages WHERE seen_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
#  POST QUEUE
# ─────────────────────────────────────────────────────────────────────────────

async def enqueue(
    arabic_text: str,
    original_text: str = "",
    media_path: Optional[str] = None,
    media_type: Optional[str] = None,
    source_channel: Optional[str] = None,
    source_message_id: Optional[int] = None,
    status: str = "pending",
) -> int:
    """Insert a new post into the queue. Returns the new queue ID."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            """INSERT INTO post_queue
               (arabic_text, original_text, media_path, media_type,
                source_channel, source_message_id, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (arabic_text, original_text, media_path, media_type,
             source_channel, source_message_id, status),
        )
        await db.commit()
        return cur.lastrowid


async def get_post(queue_id: int) -> Optional[dict]:
    """Fetch a single queue item by ID."""
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM post_queue WHERE id = ?", (queue_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_next_approved() -> Optional[dict]:
    """Return the oldest approved post, or None if queue is empty."""
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM post_queue WHERE status = 'approved' ORDER BY created_at ASC LIMIT 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_queue(status: Optional[str] = None, limit: int = 10) -> list[dict]:
    """Return queued posts filtered by status (or all active if None)."""
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                "SELECT * FROM post_queue WHERE status = ? ORDER BY created_at ASC LIMIT ?",
                (status, limit),
            )
        else:
            cur = await db.execute(
                """SELECT * FROM post_queue
                   WHERE status IN ('pending', 'approved')
                   ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            )
        return [dict(r) for r in await cur.fetchall()]


async def update_status(queue_id: int, status: str) -> None:
    async with aiosqlite.connect(DB) as db:
        posted_at = datetime.now().isoformat() if status == "posted" else None
        await db.execute(
            "UPDATE post_queue SET status = ?, posted_at = ? WHERE id = ?",
            (status, posted_at, queue_id),
        )
        await db.commit()


async def update_text(queue_id: int, arabic_text: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE post_queue SET arabic_text = ? WHERE id = ?",
            (arabic_text, queue_id),
        )
        await db.commit()


async def update_media_path(queue_id: int, media_path: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE post_queue SET media_path = ? WHERE id = ?",
            (media_path, queue_id),
        )
        await db.commit()


async def move_to_back(queue_id: int) -> None:
    """Reset created_at to now, effectively moving the post to the back of the queue."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE post_queue SET created_at = CURRENT_TIMESTAMP WHERE id = ?",
            (queue_id,),
        )
        await db.commit()


async def clear_rejected() -> int:
    """Delete all rejected posts. Returns the number of rows deleted."""
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("DELETE FROM post_queue WHERE status = 'rejected'")
        await db.commit()
        return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
#  POST LOG
# ─────────────────────────────────────────────────────────────────────────────

async def log_post(queue_id: int, channel: str, telegram_msg_id: int) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO post_log (queue_id, target_channel, telegram_message_id) VALUES (?, ?, ?)",
            (queue_id, channel, telegram_msg_id),
        )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  STATS
# ─────────────────────────────────────────────────────────────────────────────

async def bump_stats(posts: int = 0, seen: int = 0, processed: int = 0) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO stats_daily (date, posts_sent, msgs_seen, msgs_processed)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 posts_sent     = posts_sent + excluded.posts_sent,
                 msgs_seen      = msgs_seen + excluded.msgs_seen,
                 msgs_processed = msgs_processed + excluded.msgs_processed""",
            (today, posts, seen, processed),
        )
        await db.commit()


async def get_stats() -> dict:
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row

        async def scalar(sql: str) -> int:
            row = await (await db.execute(sql)).fetchone()
            return row[0] if row else 0

        total    = await scalar("SELECT COUNT(*) FROM post_log")
        pending  = await scalar("SELECT COUNT(*) FROM post_queue WHERE status='pending'")
        approved = await scalar("SELECT COUNT(*) FROM post_queue WHERE status='approved'")
        rejected = await scalar("SELECT COUNT(*) FROM post_queue WHERE status='rejected'")
        failed   = await scalar("SELECT COUNT(*) FROM post_queue WHERE status='failed'")
        seen_tot = await scalar("SELECT COUNT(*) FROM seen_messages")

        today_row = await (await db.execute(
            "SELECT * FROM stats_daily WHERE date = date('now')"
        )).fetchone()

        return {
            "total_posted":  total,
            "pending":       pending,
            "approved":      approved,
            "rejected":      rejected,
            "failed":        failed,
            "seen_messages": seen_tot,
            "today_posts":   today_row["posts_sent"]     if today_row else 0,
            "today_seen":    today_row["msgs_seen"]       if today_row else 0,
            "today_proc":    today_row["msgs_processed"]  if today_row else 0,
        }

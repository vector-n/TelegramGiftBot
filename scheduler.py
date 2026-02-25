"""
scheduler.py — Background scheduled jobs.

Jobs:
  auto_post       — Post next approved item every N minutes (configurable live)
  daily_summary   — Auto-generate and post a daily digest at a configured hour
  maintenance     — Hourly: heartbeat log + clean up old seen_messages
"""

import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config

logger = logging.getLogger(__name__)
_scheduler = AsyncIOScheduler()


# ─────────────────────────────────────────────────────────────────────────────
#  JOBS
# ─────────────────────────────────────────────────────────────────────────────

async def _auto_post_job(bot) -> None:
    from poster import post_next_in_queue
    try:
        await post_next_in_queue(bot)
    except Exception as e:
        logger.error(f"❌ auto_post_job error: {e}", exc_info=True)


async def _daily_summary_job(bot) -> None:
    from ai import daily_summary
    from poster import broadcast
    from database import DB_PATH
    import aiosqlite

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT arabic_text FROM post_queue "
                "WHERE status = 'posted' AND date(posted_at) = date('now')"
            )
            rows = await cur.fetchall()

        posts = [r["arabic_text"] for r in rows]
        summary = await daily_summary(posts)

        if summary:
            count = await broadcast(bot, summary)
            logger.info(f"📋 Daily summary posted to {count} channel(s).")
        else:
            logger.info("📭 No posts today — skipping daily summary.")
    except Exception as e:
        logger.error(f"❌ daily_summary_job error: {e}", exc_info=True)


async def _maintenance_job() -> None:
    from database import cleanup_seen, get_stats
    try:
        deleted = await cleanup_seen(days=30)
        stats   = await get_stats()
        logger.info(
            f"💓 Heartbeat | total_posted={stats['total_posted']} "
            f"approved={stats['approved']} pending={stats['pending']} "
            f"seen_cleaned={deleted}"
        )
    except Exception as e:
        logger.error(f"❌ maintenance_job error: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
#  START / STOP / RESCHEDULE
# ─────────────────────────────────────────────────────────────────────────────

def start_scheduler(bot, delay_minutes: int) -> None:
    """Initialise and start all scheduled jobs."""
    tz = pytz.timezone(config.TIMEZONE)

    # Job 1 — auto-post
    _scheduler.add_job(
        _auto_post_job,
        args=[bot],
        trigger=IntervalTrigger(minutes=delay_minutes),
        id="auto_post",
        name="Auto Post",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info(f"📅 Auto-post: every {delay_minutes} min.")

    # Job 2 — daily summary
    _scheduler.add_job(
        _daily_summary_job,
        args=[bot],
        trigger=CronTrigger(hour=config.SUMMARY_HOUR, minute=0, timezone=tz),
        id="daily_summary",
        name="Daily Summary",
        replace_existing=True,
        misfire_grace_time=600,
    )
    logger.info(f"📅 Daily summary: {config.SUMMARY_HOUR:02d}:00 {config.TIMEZONE}.")

    # Job 3 — maintenance / heartbeat
    _scheduler.add_job(
        _maintenance_job,
        trigger=IntervalTrigger(hours=1),
        id="maintenance",
        name="Maintenance",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("✅ Scheduler started.")


def reschedule_auto_post(bot, delay_minutes: int) -> None:
    """
    Change the auto-post interval on the fly (called by /setdelay).
    No restart required.
    """
    _scheduler.reschedule_job(
        "auto_post",
        trigger=IntervalTrigger(minutes=delay_minutes),
    )
    # Update the bot argument in case we need to (APScheduler keeps old args)
    _scheduler.modify_job("auto_post", args=[bot])
    logger.info(f"🔄 Auto-post rescheduled to every {delay_minutes} min.")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("🛑 Scheduler stopped.")

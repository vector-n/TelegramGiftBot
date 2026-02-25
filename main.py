"""
main.py — Entry point for the Telegram Gift Bot.

Startup order:
  1. Validate .env configuration
  2. Create media cache directory
  3. Init database + seed default settings
  4. Connect Telethon user client
  5. Build python-telegram-bot Application
  6. Start APScheduler
  7. Launch Telethon monitor as a background task
  8. Run the bot (poll for updates)
  9. Graceful shutdown on SIGINT / SIGTERM
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
# Silence noisy third-party loggers
for _noisy in ("httpx", "telethon", "apscheduler", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    import config
    import client as telethon_client
    import monitor
    from bot import build_app
    from database import init_db, init_settings, get_setting
    from scheduler import start_scheduler, stop_scheduler

    logger.info("=" * 60)
    logger.info("🚀  Starting Telegram Gift Bot …")
    logger.info("=" * 60)

    # 1. Validate config
    config.validate()

    # 2. Ensure media cache directory exists
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Database
    await init_db()
    await init_settings()

    # 4. Telethon user client
    await telethon_client.start()

    # 5. Build bot application
    app = build_app()

    # 6. Inject bot reference into monitor for admin notifications
    monitor.set_bot(app.bot)

    # 7. Scheduler
    delay = int(await get_setting("post_delay_minutes", "30"))
    start_scheduler(app.bot, delay)

    # 8. Telethon monitor (background task)
    monitor_task = asyncio.create_task(
        monitor.start_monitor(),
        name="channel_monitor",
    )

    # 9. Graceful shutdown using asyncio-safe signal handling
    stop_event = asyncio.Event()

    def _request_shutdown():
        logger.info("🛑  Shutdown signal received …")
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        import signal
        loop.add_signal_handler(signal.SIGINT,  _request_shutdown)
        loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
    except NotImplementedError:
        # Windows doesn't support loop.add_signal_handler for all signals
        pass

    # 10. Start bot polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("✅  Bot is running. Send Ctrl+C to stop.")

    # Wait until shutdown is requested
    await stop_event.wait()

    # 11. Shutdown sequence
    logger.info("🛑  Shutting down …")

    stop_scheduler()

    await app.updater.stop()
    await app.stop()
    await app.shutdown()

    # Disconnecting the Telethon client causes run_until_disconnected() to
    # return, which completes the monitor_task cleanly.
    await telethon_client.stop()

    try:
        await asyncio.wait_for(monitor_task, timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        monitor_task.cancel()

    logger.info("✅  Bot stopped cleanly. Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())

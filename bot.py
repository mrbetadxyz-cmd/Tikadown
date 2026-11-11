import asyncio
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass
import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
import time
import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject, Update

from config import settings
from handlers import router as main_router
from handlers.downloader import has_active_transfers

# Setup logging with size-based rotation (max 10MB x 4 files)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            "bot.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger(__name__)

async def set_commands(bot: Bot):
    """
    Register bot commands in Telegram interface.
    """
    commands = [
        BotCommand(command="start", description="⚡ Start the bot"),
        BotCommand(command="help", description="📖 How to use"),
        BotCommand(command="stats", description="📊 Your download stats"),
    ]
    await bot.set_my_commands(commands)

import datetime
from pathlib import Path
from aiogram.types import FSInputFile

OWNER_ID = settings.owner_id

def seconds_until_midnight():
    now = datetime.datetime.now()
    tomorrow = now + datetime.timedelta(days=1)
    midnight = datetime.datetime.combine(tomorrow, datetime.time.min)
    return (midnight - now).total_seconds()

async def send_daily_backup(bot: Bot):
    if not OWNER_ID or settings.worker_url:
        return
    db_file = Path("database.db")
    while True:
        sleep_secs = seconds_until_midnight()
        if sleep_secs < 60:
            sleep_secs = 86400
        logger.info(f"Daily database backup scheduled in {sleep_secs} seconds (at midnight).")
        await asyncio.sleep(sleep_secs)
        
        if db_file.exists():
            try:
                await bot.send_document(
                    chat_id=OWNER_ID,
                    document=FSInputFile(db_file),
                    caption="💾 Daily Database Backup | Soft4P Bot"
                )
                logger.info("Daily backup sent to owner successfully.")
            except Exception as e:
                logger.error(f"Failed to send daily backup: {e}")


class ActivityTrackingMiddleware(BaseMiddleware):
    """Tracks recent user updates and active handler count for On-Demand auto-sleep."""

    def __init__(self, state: dict):
        self.state = state

    async def __call__(self, handler, event: TelegramObject, data: dict):
        self.state["last_activity"] = time.monotonic()
        self.state["in_flight"] += 1
        try:
            return await handler(event, data)
        finally:
            self.state["in_flight"] = max(0, self.state["in_flight"] - 1)
            self.state["last_activity"] = time.monotonic()


async def _pop_and_replay_worker_updates(bot: Bot, dp: Dispatcher, activity_state: dict):
    """Fetch buffered Telegram updates from Cloudflare Worker and feed them to Dispatcher."""
    worker_base = settings.worker_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.worker_secret}"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(f"{worker_base}/pop-updates", headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Worker /pop-updates returned status {resp.status}")
                    return
                data = await resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch buffered updates from Worker: {e}")
        return

    items = data.get("updates") or []
    if not items:
        return

    logger.info(f"Replaying {len(items)} buffered update(s) from Cloudflare Worker...")
    activity_state["last_activity"] = time.monotonic()

    async def _run_item(item: dict):
        chat_id = item.get("chat_id")
        status_msg_id = item.get("status_msg_id")
        if chat_id and status_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
            except Exception:
                pass
        try:
            upd = Update.model_validate(item["update"])
            await dp.feed_update(bot, upd)
        except Exception as e:
            logger.error(f"Failed to replay buffered update: {e}")

    for item in items:
        asyncio.create_task(_run_item(item))


async def _idle_watchdog(dp: Dispatcher, activity_state: dict, start_ts: float):
    """Stops polling cleanly after inactivity or max runtime when running in On-Demand mode."""
    while True:
        await asyncio.sleep(15)
        now = time.monotonic()
        if activity_state["in_flight"] > 0 or has_active_transfers():
            activity_state["last_activity"] = now
            continue

        idle_exceeded = (
            settings.idle_timeout_seconds > 0
            and (now - activity_state["last_activity"]) >= settings.idle_timeout_seconds
        )
        max_run_exceeded = (
            settings.max_run_seconds > 0
            and (now - start_ts) >= settings.max_run_seconds
        )

        if idle_exceeded or max_run_exceeded:
            reason = "idle timeout" if idle_exceeded else "max runtime"
            logger.info(f"On-Demand {reason} reached. Stopping bot cleanly to save runner minutes...")
            try:
                await dp.stop_polling()
            except Exception as e:
                logger.warning(f"stop_polling raised: {e}")
            break

def _purge_stale_downloads(download_dir: Path, max_age_seconds: int) -> tuple[int, int]:
    """
    Synchronously removes temporary/orphaned files older than max_age_seconds
    from download_dir. Returns (removed_count, freed_bytes).
    """
    if not download_dir.exists():
        return 0, 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    freed = 0
    for item in download_dir.iterdir():
        try:
            if not item.is_file():
                continue
            stat = item.stat()
            if stat.st_mtime < cutoff:
                size = stat.st_size
                item.unlink(missing_ok=True)
                removed += 1
                freed += size
        except Exception as e:
            logger.warning(f"Failed to remove stale temp file {item}: {e}")
    return removed, freed


async def cleanup_downloads_periodically(max_age_hours: int = 2, interval_hours: int = 6):
    """
    Periodically sweeps the downloads directory to remove orphaned temp files
    left behind by failed or interrupted downloads without blocking the event loop.
    """
    download_dir = Path(settings.download_dir)
    max_age_seconds = max_age_hours * 3600
    interval_seconds = interval_hours * 3600
    while True:
        try:
            removed, freed = await asyncio.to_thread(
                _purge_stale_downloads, download_dir, max_age_seconds
            )
            if removed > 0:
                freed_mb = freed / (1024 * 1024)
                logger.info(
                    f"Temp cleanup removed {removed} stale file(s), freed {freed_mb:.2f} MB."
                )
        except Exception as e:
            logger.error(f"Temp cleanup sweep failed: {e}")
        await asyncio.sleep(interval_seconds)


async def main():
    # Verify bot token
    if not settings.bot_token or settings.bot_token == "your_telegram_bot_token_here":
        logger.error("BOT_TOKEN is not set in the .env file! Please set it and restart.")
        print("[Error] BOT_TOKEN is not set in the .env file.")
        print("Please edit .env and replace 'your_telegram_bot_token_here' with your real bot token from @BotFather.")
        sys.exit(1)

    # Initialize bot and dispatcher
    bot = Bot(token=settings.bot_token)
    
    # Using MemoryStorage for simple FSM state tracking
    dp = Dispatcher(storage=MemoryStorage())

    on_demand_mode = bool(settings.worker_url)
    activity_state = {"last_activity": time.monotonic(), "in_flight": 0}
    if on_demand_mode:
        dp.update.outer_middleware(ActivityTrackingMiddleware(activity_state))
    
    # Register outer middlewares for checking channel join status
    from utils.force_join import ForceJoinMiddleware
    dp.message.outer_middleware(ForceJoinMiddleware())
    dp.callback_query.outer_middleware(ForceJoinMiddleware())
    
    # Include all routers
    dp.include_router(main_router)
    
    # Start userbot helper
    from utils.userbot import start_userbot, userbot_client
    await start_userbot()

    # Set bot commands
    await set_commands(bot)
    
    # Start background tasks for daily backups and periodic temp cleanup
    backup_task = asyncio.create_task(send_daily_backup(bot))
    cleanup_task = asyncio.create_task(cleanup_downloads_periodically())
    watchdog_task = None
    
    logger.info(
        "Starting Soft4P Telegram Bot (%s mode)...",
        "On-Demand Worker" if on_demand_mode else "Standard 24/7",
    )
    print("Soft4P Bot is starting and polling updates...")
    
    try:
        # In On-Demand mode, keep any updates arriving right as webhook is removed
        await bot.delete_webhook(drop_pending_updates=not on_demand_mode)

        if on_demand_mode:
            asyncio.create_task(_pop_and_replay_worker_updates(bot, dp, activity_state))
            watchdog_task = asyncio.create_task(
                _idle_watchdog(dp, activity_state, time.monotonic())
            )

        # Start polling
        await dp.start_polling(bot)
    finally:
        for t in (backup_task, cleanup_task, watchdog_task):
            if t and not t.done():
                t.cancel()

        if on_demand_mode:
            webhook_endpoint = f"{settings.worker_url.rstrip('/')}/webhook"
            try:
                await bot.set_webhook(
                    url=webhook_endpoint,
                    secret_token=settings.worker_secret or None,
                    drop_pending_updates=False,
                )
                logger.info(f"Restored Telegram webhook to {webhook_endpoint}")
            except Exception as e:
                logger.error(f"Failed to restore Telegram webhook: {e}")

        await bot.session.close()
        try:
            await userbot_client.disconnect()
        except:
            pass
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")

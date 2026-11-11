import asyncio
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass
import logging
from logging.handlers import RotatingFileHandler
import sys
import time
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import settings
from handlers import router as main_router

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
    asyncio.create_task(send_daily_backup(bot))
    asyncio.create_task(cleanup_downloads_periodically())
    
    logger.info("Starting Soft4P Telegram Bot...")
    print("Soft4P Bot is starting and polling updates...")
    
    try:
        # Skip accumulated updates (avoids spam when bot starts)
        await bot.delete_webhook(drop_pending_updates=True)
        # Start polling
        await dp.start_polling(bot)
    finally:
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

import sqlite3
import asyncio
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
DB_FILE = Path("database.db")

def init_db():
    """
    Initialize SQLite database tables.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Enable Write-Ahead Logging (WAL) mode for high concurrent read/write performance
        cursor.execute("PRAGMA journal_mode=WAL;")
        
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                first_seen TIMESTAMP
            )
        """)
        
        # Create downloads table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                platform TEXT,
                url TEXT,
                timestamp TIMESTAMP
            )
        """)
        
        # Create index on user_id to speed up user statistics queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_user_id ON downloads (user_id);")
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully with WAL mode.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

# Run database setup synchronously
init_db()

def _add_user(user_id: int, username: str, full_name: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Insert or ignore to prevent duplicates
        cursor.execute("""
            INSERT OR IGNORE INTO users (user_id, username, full_name, first_seen)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, full_name, datetime.now()))
        
        # Update user details (in case they changed username/name)
        cursor.execute("""
            UPDATE users 
            SET username = ?, full_name = ? 
            WHERE user_id = ?
        """, (username, full_name, user_id))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error adding user to database: {e}")

async def add_user(user_id: int, username: str, full_name: str):
    await asyncio.to_thread(_add_user, user_id, username, full_name)

def _add_download(user_id: int, platform: str, url: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO downloads (user_id, platform, url, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, platform, url, datetime.now()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error adding download to database: {e}")

async def add_download(user_id: int, platform: str, url: str):
    await asyncio.to_thread(_add_download, user_id, platform, url)

def _get_stats():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Total users
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        # Total downloads
        cursor.execute("SELECT COUNT(*) FROM downloads")
        total_downloads = cursor.fetchone()[0]
        
        # Downloads by platform
        cursor.execute("SELECT platform, COUNT(*) FROM downloads GROUP BY platform")
        platform_stats = dict(cursor.fetchall())
        
        conn.close()
        return {
            "total_users": total_users,
            "total_downloads": total_downloads,
            "platforms": platform_stats
        }
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        return {
            "total_users": 0,
            "total_downloads": 0,
            "platforms": {}
        }

async def get_stats():
    return await asyncio.to_thread(_get_stats)

def _get_user_stats(user_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        # Total downloads for the user
        cursor.execute("SELECT COUNT(*) FROM downloads WHERE user_id = ?", (user_id,))
        total_downloads = cursor.fetchone()[0]

        # Downloads by platform for the user
        cursor.execute(
            "SELECT platform, COUNT(*) FROM downloads WHERE user_id = ? GROUP BY platform",
            (user_id,),
        )
        platforms = dict(cursor.fetchall())

        conn.close()
        return {
            "total_downloads": total_downloads,
            "platforms": platforms,
        }
    except Exception as e:
        logger.error(f"Error getting user statistics: {e}")
        return {
            "total_downloads": 0,
            "platforms": {},
        }

async def get_user_stats(user_id: int):
    return await asyncio.to_thread(_get_user_stats, user_id)

def _get_all_users():
    try:
        import sqlite3
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    except Exception as e:
        logger.error(f"Error getting all users: {e}")
        return []

async def get_all_users():
    return await asyncio.to_thread(_get_all_users)

def _get_daily_youtube_long_downloads(user_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cursor.execute("""
            SELECT COUNT(*) FROM downloads 
            WHERE user_id = ? AND platform = 'youtube' AND timestamp >= ?
        """, (user_id, today_start))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error getting daily youtube downloads: {e}")
        return 0

async def get_daily_youtube_long_downloads(user_id: int):
    return await asyncio.to_thread(_get_daily_youtube_long_downloads, user_id)

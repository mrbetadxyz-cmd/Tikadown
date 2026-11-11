import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str
    download_dir: Path = Path("downloads")
    max_file_size_mb: int = 2000
    owner_id: int = 1334814727
    userbot_api_id: int = 31044085
    userbot_api_hash: str = "c2e4f3578e2eae758fbc3b19fa7e94ad"
    userbot_channel_id: int = -1004462404451
    telethon_session: str = ""
    youtube_long_video_limit: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings
settings = Settings()

# Ensure download directory exists
settings.download_dir.mkdir(parents=True, exist_ok=True)

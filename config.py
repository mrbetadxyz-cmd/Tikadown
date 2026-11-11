from pathlib import Path
from typing import Any
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    download_dir: Path = Path("downloads")
    max_file_size_mb: int = 2000
    owner_id: int = 0
    log_group_id: int = 0
    userbot_api_id: int = 0
    userbot_api_hash: str = ""
    userbot_channel_id: int = 0
    telethon_session: str = ""
    youtube_long_video_limit: int = 3

    # On-Demand GitHub Actions + Cloudflare Worker settings (leave WORKER_URL empty for 24/7 local/VPS mode)
    worker_url: str = ""
    worker_secret: str = ""
    idle_timeout_seconds: int = 300
    max_run_seconds: int = 5400

    @field_validator(
        "max_file_size_mb",
        "owner_id",
        "log_group_id",
        "userbot_api_id",
        "userbot_channel_id",
        "youtube_long_video_limit",
        "idle_timeout_seconds",
        "max_run_seconds",
        mode="before",
    )
    @classmethod
    def _empty_str_to_default(cls, v: Any, info: Any) -> Any:
        if isinstance(v, str) and v.strip() == "":
            field_info = cls.model_fields.get(info.field_name)
            return field_info.default if field_info is not None else 0
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instantiate settings
settings = Settings()

# Ensure download directory exists
settings.download_dir.mkdir(parents=True, exist_ok=True)

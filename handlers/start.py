import asyncio
import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InputRichMessage

logger = logging.getLogger(__name__)
router = Router()


def make_bot_footer(subtitle: str) -> dict:
    """Builds a rich footer block with Soft4P hyperlinked to https://t.me/soft4pbot."""
    return {
        "type": "footer",
        "text": [
            {"type": "url", "text": "Soft4P", "url": "https://t.me/soft4pbot"},
            f" • {subtitle}"
        ]
    }


def get_welcome_blocks(first_name: str) -> list[dict]:
    """
    Builds the native Telegram rich message blocks for the Welcome Card.
    Features native heading, intro paragraph, supported platforms table,
    collapsible how-to accordion, divider, vertical in-text buttons, and footer.
    """
    return [
        {"type": "heading", "size": 1, "text": "⚡ SOFT4P Premium Downloader"},
        {
            "type": "paragraph",
            "text": (
                f"Hey {first_name}! Welcome aboard.\n\n"
                f"Download videos, music, and photos from your favorite platforms — "
                f"fast, watermark-free, and in top quality."
            )
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Platform", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Quality", "is_header": True, "align": "center", "valign": "middle"},
                    {"text": "Status", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎥 YouTube", "align": "left", "valign": "middle"},
                    {"text": "1080p / MP3", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎵 TikTok", "align": "left", "valign": "middle"},
                    {"text": "No Watermark", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📸 Instagram", "align": "left", "valign": "middle"},
                    {"text": "Reels / HD", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📌 Pinterest", "align": "left", "valign": "middle"},
                    {"text": "Original Photo/Video", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📘 Facebook", "align": "left", "valign": "middle"},
                    {"text": "HD Video / Reels", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📻 Music Search", "align": "left", "valign": "middle"},
                    {"text": "320k Audio", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🌐 Any Website", "align": "left", "valign": "middle"},
                    {"text": "Multi Quality • MP4", "align": "center", "valign": "middle"},
                    {"text": "⚡ Free", "align": "center", "valign": "middle"}
                ]
            ]
        },
        {
            "type": "details",
            "summary": "📖 Quick Start Guide (Tap to expand)",
            "is_open": False,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        "1. Copy any video or photo link from supported apps or any website.\n"
                        "2. For other websites, tap the Share button on the video page and copy the direct video link (not an embed link).\n"
                        "3. Paste and send the link directly here.\n"
                        "4. Select your preferred quality from the in-text buttons!\n\n"
                        "💡 Direct Music: Just type any song title or artist name to get the MP3 instantly."
                    )
                }
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "📖  How to Use", "callback_data": "nav_help", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "🌐  Supported Sites", "callback_data": "nav_platforms", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "📊  My Stats", "callback_data": "nav_mystats", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "💬  Support Channel", "url": "https://t.me/soft4pbot", "style": "primary"}]},
        make_bot_footer("Fast, Watermark-Free Downloader")
    ]


def get_help_blocks() -> list[dict]:
    """
    Builds the native Telegram rich message blocks for the Help / How-To screen.
    """
    return [
        {"type": "heading", "size": 1, "text": "📖 How to Use Soft4P"},
        {
            "type": "paragraph",
            "text": "Soft4P delivers high-speed, watermark-free media downloads directly inside Telegram."
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Type", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "How to Send", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Output", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📻 Music", "align": "left", "valign": "middle"},
                    {"text": "Type song / artist name", "align": "left", "valign": "middle"},
                    {"text": "320k MP3", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎬 Videos", "align": "left", "valign": "middle"},
                    {"text": "Paste video post link", "align": "left", "valign": "middle"},
                    {"text": "1080p MP4", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📸 Photos", "align": "left", "valign": "middle"},
                    {"text": "Paste slideshow / pin link", "align": "left", "valign": "middle"},
                    {"text": "HD Album", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎙️ Voice", "align": "left", "valign": "middle"},
                    {"text": "Pick voice option from menu", "align": "left", "valign": "middle"},
                    {"text": "Voice Note", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🌐 Other Sites", "align": "left", "valign": "middle"},
                    {"text": "Tap Share -> Copy direct video link", "align": "left", "valign": "middle"},
                    {"text": "Multi Quality", "align": "center", "valign": "middle"}
                ]
            ]
        },
        {
            "type": "details",
            "summary": "🌐 Downloading from Other Websites (Tap to expand)",
            "is_open": True,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        "• In addition to major social platforms, you can download videos from almost any website.\n"
                        "• Open the video page, tap the Share button, copy the direct video link, and send it here.\n"
                        "• Please send the normal video page link — do NOT send embed links."
                    )
                }
            ]
        },
        {
            "type": "details",
            "summary": "💡 Key Features & Tips (Tap to expand)",
            "is_open": False,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        "• Unlimited daily song downloads\n"
                        "• Automatic 1:1 square album art & tags\n"
                        "• TikTok photo slideshows sent as native Telegram albums\n"
                        "• Watermark-free, clean video streams"
                    )
                }
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "🌐  Supported Sites", "callback_data": "nav_platforms", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "⬅️  Back to Home", "callback_data": "nav_home", "style": "danger"}]},
        make_bot_footer("User Guide")
    ]


def get_platforms_blocks() -> list[dict]:
    """
    Builds the native Telegram rich message blocks for Supported Platforms.
    """
    return [
        {"type": "heading", "size": 1, "text": "🌐 Supported Platforms"},
        {
            "type": "paragraph",
            "text": "All major platforms are supported with high resolution and watermark removal:"
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Platform", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Media Types", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Quality", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎥 YouTube", "align": "left", "valign": "middle"},
                    {"text": "Videos, Shorts, Music", "align": "left", "valign": "middle"},
                    {"text": "1080p / MP3", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎵 TikTok", "align": "left", "valign": "middle"},
                    {"text": "Videos, Albums, Audio", "align": "left", "valign": "middle"},
                    {"text": "No Watermark", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📸 Instagram", "align": "left", "valign": "middle"},
                    {"text": "Reels, Posts, Stories", "align": "left", "valign": "middle"},
                    {"text": "Original HD", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📌 Pinterest", "align": "left", "valign": "middle"},
                    {"text": "Video Pins & Photos", "align": "left", "valign": "middle"},
                    {"text": "High-Res", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📘 Facebook", "align": "left", "valign": "middle"},
                    {"text": "Public Videos & Reels", "align": "left", "valign": "middle"},
                    {"text": "HD Video", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🐦 Twitter / X", "align": "left", "valign": "middle"},
                    {"text": "Clips & Animated GIFs", "align": "left", "valign": "middle"},
                    {"text": "HD MP4", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📻 Music Search", "align": "left", "valign": "middle"},
                    {"text": "Any Song / Artist Name", "align": "left", "valign": "middle"},
                    {"text": "320k Audio", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🌐 Any Website", "align": "left", "valign": "middle"},
                    {"text": "Public Video Pages (Share Link)", "align": "left", "valign": "middle"},
                    {"text": "Multi Quality", "align": "center", "valign": "middle"}
                ]
            ]
        },
        {
            "type": "details",
            "summary": "⚡ Need Help with a Link?",
            "is_open": False,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "Make sure your post or video is publicly accessible. Private accounts or age-restricted posts cannot be retrieved."
                }
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "📖  How to Use", "callback_data": "nav_help", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "⬅️  Back to Home", "callback_data": "nav_home", "style": "danger"}]},
        make_bot_footer("Supported Sites")
    ]


def get_stats_blocks(full_name: str, total_dl: int, platforms: dict) -> list[dict]:
    """
    Builds the native Telegram rich message blocks for User Stats.
    """
    known_keys = {"music", "tiktok", "instagram", "youtube", "pinterest", "facebook", "twitter", "soundcloud"}
    other_count = sum(
        cnt for name, cnt in (platforms or {}).items()
        if name and name.lower() not in known_keys
    )
    return [
        {"type": "heading", "size": 1, "text": "📊 Your Download Statistics"},
        {
            "type": "paragraph",
            "text": f"👤 Account: {full_name}\n📥 Total Media Downloaded: {total_dl}"
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Category", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Downloads", "is_header": True, "align": "center", "valign": "middle"},
                    {"text": "Status", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📻 Music / Songs", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("music", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎵 TikTok", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("tiktok", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📸 Instagram", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("instagram", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎥 YouTube", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("youtube", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📌 Pinterest", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("pinterest", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📘 Facebook", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("facebook", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🐦 Twitter / X", "align": "left", "valign": "middle"},
                    {"text": str(platforms.get("twitter", 0)), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🌐 Other Websites", "align": "left", "valign": "middle"},
                    {"text": str(other_count), "align": "center", "valign": "middle"},
                    {"text": "Active", "align": "center", "valign": "middle"}
                ]
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "🔄  Refresh Stats", "callback_data": "nav_mystats", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "⬅️  Back to Home", "callback_data": "nav_home", "style": "danger"}]},
        make_bot_footer("Realtime Usage")
    ]


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """
    Handler for /start command.
    Sends native Telegram rich message blocks welcome card with tables, details, and in-text buttons.
    """
    from utils.database import add_user
    await add_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name
    )

    blocks = get_welcome_blocks(message.from_user.first_name)
    await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=InputRichMessage.model_validate({"blocks": blocks})
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    """
    Handler for /help command with native rich message blocks.
    """
    blocks = get_help_blocks()
    await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=InputRichMessage.model_validate({"blocks": blocks})
    )


# ── Navigation Callback Handlers ──────────────────────────────

@router.callback_query(lambda c: c.data == "nav_home")
async def nav_home(callback: types.CallbackQuery):
    blocks = get_welcome_blocks(callback.from_user.first_name)
    try:
        await callback.message.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": blocks})
        )
    except Exception as e:
        logger.warning(f"nav_home edit failed: {e}")
    await callback.answer()


@router.callback_query(lambda c: c.data == "nav_help")
async def nav_help(callback: types.CallbackQuery):
    blocks = get_help_blocks()
    try:
        await callback.message.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": blocks})
        )
    except Exception as e:
        logger.warning(f"nav_help edit failed: {e}")
    await callback.answer()


@router.callback_query(lambda c: c.data == "nav_platforms")
async def nav_platforms(callback: types.CallbackQuery):
    blocks = get_platforms_blocks()
    try:
        await callback.message.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": blocks})
        )
    except Exception as e:
        logger.warning(f"nav_platforms edit failed: {e}")
    await callback.answer()


@router.callback_query(lambda c: c.data == "nav_mystats")
async def nav_mystats(callback: types.CallbackQuery):
    from utils.database import get_user_stats

    user_stats = await get_user_stats(callback.from_user.id)
    total_dl = user_stats.get("total_downloads", 0)
    platforms = user_stats.get("platforms", {})

    blocks = get_stats_blocks(
        full_name=callback.from_user.full_name,
        total_dl=total_dl,
        platforms=platforms
    )
    try:
        await callback.message.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": blocks})
        )
    except Exception as e:
        logger.warning(f"nav_mystats edit failed: {e}")
    await callback.answer()


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """
    Handler for /stats command with native rich message blocks.
    """
    from utils.database import get_user_stats

    user_stats = await get_user_stats(message.from_user.id)
    total_dl = user_stats.get("total_downloads", 0)
    platforms = user_stats.get("platforms", {})

    blocks = get_stats_blocks(
        full_name=message.from_user.full_name,
        total_dl=total_dl,
        platforms=platforms
    )
    await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=InputRichMessage.model_validate({"blocks": blocks})
    )


@router.callback_query(lambda c: c.data == "check_join_status")
async def check_join_status(callback: types.CallbackQuery):
    from utils.force_join import get_unjoined_channels, get_join_text, get_join_keyboard

    unjoined = await get_unjoined_channels(callback.bot, callback.from_user.id)
    if not unjoined:
        await callback.answer("✅ Thank you for joining! You can now use the bot.", show_alert=True)
        blocks = get_welcome_blocks(callback.from_user.first_name)
        try:
            await callback.message.edit_text(
                rich_message=InputRichMessage.model_validate({"blocks": blocks})
            )
        except Exception as e:
            logger.warning(f"check_join_status edit failed: {e}")
    else:
        unjoined_str = ", ".join([ch.get("username", "") for ch in unjoined])
        await callback.answer(f"❌ You have not joined {unjoined_str} yet. Please join and click Verify Membership!", show_alert=True)
        try:
            await callback.message.edit_text(
                get_join_text(unjoined),
                parse_mode="HTML",
                reply_markup=get_join_keyboard(unjoined)
            )
        except Exception:
            pass

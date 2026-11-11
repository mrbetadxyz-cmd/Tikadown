import os
import datetime
import logging
from pathlib import Path
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InputRichMessage, FSInputFile

from utils.database import get_stats, get_all_users
from config import settings
from handlers.start import make_bot_footer

logger = logging.getLogger(__name__)
router = Router()
OWNER_ID = settings.owner_id
KNOWN_PLATFORMS = {"tiktok", "instagram", "pinterest", "facebook", "youtube", "music", "twitter", "soundcloud"}


def _split_other_platforms(platforms: dict) -> dict[str, int]:
    """Return {site: count} for downloads outside the core social/music platforms."""
    return {
        str(name): int(cnt)
        for name, cnt in (platforms or {}).items()
        if name and str(name).lower() not in KNOWN_PLATFORMS and int(cnt) > 0
    }


def get_other_sites_blocks(platforms: dict, total_downloads: int) -> list[dict]:
    """
    Builds native Telegram rich message blocks showing dynamically tracked external sites
    and their downloaded video counts, matching the normal Admin breakdown table.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    other_map = _split_other_platforms(platforms)
    other_total = sum(other_map.values())
    sorted_sites = sorted(other_map.items(), key=lambda x: (-x[1], x[0].lower()))

    rows = [
        [
            {"text": "Website", "is_header": True, "align": "left", "valign": "middle"},
            {"text": "Videos", "is_header": True, "align": "center", "valign": "middle"},
            {"text": "Share", "is_header": True, "align": "center", "valign": "middle"}
        ]
    ]

    share_base = max(other_total, 1)
    max_rows = 25
    for site_name, count in sorted_sites[:max_rows]:
        display_site = site_name.capitalize()
        rows.append([
            {"text": f"🌐 {display_site}", "align": "left", "valign": "middle"},
            {"text": str(count), "align": "center", "valign": "middle"},
            {"text": f"{(count / share_base * 100):.1f}%", "align": "center", "valign": "middle"}
        ])

    if not sorted_sites:
        rows.append([
            {"text": "🌐 No other sites yet", "align": "left", "valign": "middle"},
            {"text": "0", "align": "center", "valign": "middle"},
            {"text": "0.0%", "align": "center", "valign": "middle"}
        ])

    overall_share = (other_total / max(total_downloads, 1) * 100)
    extra_note = (
        f"Showing top {max_rows} of {len(sorted_sites)} sites."
        if len(sorted_sites) > max_rows
        else f"Tracking {len(sorted_sites)} active external sites."
    )

    return [
        {"type": "heading", "size": 1, "text": "🌐 Other Sites Analytics"},
        {
            "type": "paragraph",
            "text": (
                f"📥 Total Other-Site Videos: {other_total} ({overall_share:.1f}% of all downloads)\n"
                f"📊 {extra_note}\n"
                f"🕐 Updated: {now}"
            )
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "caption": "Other Websites Breakdown",
            "cells": rows
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "🔄  Refresh Other Sites", "callback_data": "admin_other_sites", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "⬅️  Back to Dashboard", "callback_data": "admin_refresh", "style": "danger"}]},
        make_bot_footer("Admin External Sites Analytics")
    ]


def get_admin_dashboard_blocks(total_users: int, total_downloads: int, platforms: dict) -> list[dict]:
    """
    Builds native Telegram rich message blocks for the Admin Dashboard.
    Features native heading, system metrics table, platform analytics table,
    divider, vertical in-text buttons, and footer.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    tiktok_count = platforms.get("tiktok", 0)
    instagram_count = platforms.get("instagram", 0)
    pinterest_count = platforms.get("pinterest", 0)
    facebook_count = platforms.get("facebook", 0)
    youtube_count = platforms.get("youtube", 0)
    music_count = platforms.get("music", 0)
    twitter_count = platforms.get("twitter", 0)
    other_count = sum(_split_other_platforms(platforms).values())

    tot = max(total_downloads, 1)

    return [
        {"type": "heading", "size": 1, "text": "🛡️ SOFT4P Admin Dashboard"},
        {
            "type": "paragraph",
            "text": "Realtime system metrics and usage statistics for Soft4P Telegram Bot."
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "System Metric", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Count", "is_header": True, "align": "center", "valign": "middle"},
                    {"text": "Status", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "👥 Registered Users", "align": "left", "valign": "middle"},
                    {"text": str(total_users), "align": "center", "valign": "middle"},
                    {"text": "🟢 Active", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📥 Total Downloads", "align": "left", "valign": "middle"},
                    {"text": str(total_downloads), "align": "center", "valign": "middle"},
                    {"text": "✅ Success", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "⚡ Bot Health", "align": "left", "valign": "middle"},
                    {"text": "Online", "align": "center", "valign": "middle"},
                    {"text": "🟢 Optimal", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🕐 Last Synced", "align": "left", "valign": "middle"},
                    {"text": now, "align": "center", "valign": "middle"},
                    {"text": "⚡ Live", "align": "center", "valign": "middle"}
                ]
            ]
        },
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "caption": "Platform Usage Breakdown",
            "cells": [
                [
                    {"text": "Platform", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Downloads", "is_header": True, "align": "center", "valign": "middle"},
                    {"text": "Share", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎵 TikTok", "align": "left", "valign": "middle"},
                    {"text": str(tiktok_count), "align": "center", "valign": "middle"},
                    {"text": f"{(tiktok_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📸 Instagram", "align": "left", "valign": "middle"},
                    {"text": str(instagram_count), "align": "center", "valign": "middle"},
                    {"text": f"{(instagram_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📌 Pinterest", "align": "left", "valign": "middle"},
                    {"text": str(pinterest_count), "align": "center", "valign": "middle"},
                    {"text": f"{(pinterest_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📘 Facebook", "align": "left", "valign": "middle"},
                    {"text": str(facebook_count), "align": "center", "valign": "middle"},
                    {"text": f"{(facebook_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🎥 YouTube", "align": "left", "valign": "middle"},
                    {"text": str(youtube_count), "align": "center", "valign": "middle"},
                    {"text": f"{(youtube_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "📻 Music Search", "align": "left", "valign": "middle"},
                    {"text": str(music_count), "align": "center", "valign": "middle"},
                    {"text": f"{(music_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🐦 Twitter / X", "align": "left", "valign": "middle"},
                    {"text": str(twitter_count), "align": "center", "valign": "middle"},
                    {"text": f"{(twitter_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "🌐 Other Sites", "align": "left", "valign": "middle"},
                    {"text": str(other_count), "align": "center", "valign": "middle"},
                    {"text": f"{(other_count / tot * 100):.1f}%", "align": "center", "valign": "middle"}
                ]
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "🔄  Refresh Stats", "callback_data": "admin_refresh", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "🌐  Other Sites", "callback_data": "admin_other_sites", "style": "primary"}]},
        {"type": "buttons", "buttons": [{"text": "📤  Export Database", "callback_data": "admin_export_db", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "📢  Broadcast Message", "callback_data": "admin_broadcast_info", "style": "primary"}]},
        make_bot_footer("Admin Management Panel")
    ]


@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """
    Handler for /admin command.
    Only allows access to the configured bot owner.
    """
    if message.from_user.id != OWNER_ID:
        return

    stats = await get_stats()
    blocks = get_admin_dashboard_blocks(
        total_users=stats.get("total_users", 0),
        total_downloads=stats.get("total_downloads", 0),
        platforms=stats.get("platforms", {})
    )
    await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=InputRichMessage.model_validate({"blocks": blocks})
    )


@router.callback_query(lambda c: c.data == "admin_refresh")
async def admin_refresh(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔ Access denied.", show_alert=True)
        return

    stats = await get_stats()
    blocks = get_admin_dashboard_blocks(
        total_users=stats.get("total_users", 0),
        total_downloads=stats.get("total_downloads", 0),
        platforms=stats.get("platforms", {})
    )
    try:
        await callback.message.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": blocks})
        )
    except Exception as e:
        logger.warning(f"admin_refresh edit failed: {e}")
    await callback.answer("✅ Stats refreshed!")


@router.callback_query(lambda c: c.data == "admin_other_sites")
async def admin_other_sites(callback: types.CallbackQuery):
    """Display dynamically updated table of external sites and downloaded video counts."""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔ Access denied.", show_alert=True)
        return

    stats = await get_stats()
    blocks = get_other_sites_blocks(
        platforms=stats.get("platforms", {}),
        total_downloads=stats.get("total_downloads", 0)
    )
    try:
        await callback.message.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": blocks})
        )
    except Exception as e:
        logger.warning(f"admin_other_sites edit failed: {e}")
    await callback.answer("✅ Other Sites loaded!")


@router.callback_query(lambda c: c.data == "admin_export_db")
async def admin_export_db(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔ Access denied.", show_alert=True)
        return

    db_file = Path("database.db")
    if db_file.exists():
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        caption_text = f"💾 <b>Database Snapshot</b> • Soft4P\n🕐 Generated: {now_str}"
        await callback.message.reply_document(
            document=FSInputFile(db_file),
            caption=caption_text,
            parse_mode="HTML"
        )
        await callback.answer("✅ Database exported!")
    else:
        await callback.answer("❌ Database file not found.", show_alert=True)


@router.callback_query(lambda c: c.data == "admin_broadcast_info")
async def admin_broadcast_info(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔ Access denied.", show_alert=True)
        return
    await callback.answer(
        "📢 Broadcast Commands:\n\n1. /broadcast <text>\n2. Reply to video/media with /broadcastv [caption]",
        show_alert=True
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    """
    Handler for /broadcast command.
    Sends a message to all registered users in the database.
    """
    import asyncio
    if message.from_user.id != OWNER_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply(
            "⚠️ <b>Invalid Usage</b>\n\nFormat:\n<code>/broadcast Your message here</code>",
            parse_mode="HTML"
        )
        return

    broadcast_text = args[1]
    users = await get_all_users()
    if not users:
        await message.reply("❌ No registered users found in the database.")
        return

    status_msg = await message.reply(f"📢 Starting broadcast to {len(users)} users... ⏳")
    success_count = 0
    fail_count = 0

    for user_id in users:
        try:
            await message.bot.send_message(chat_id=user_id, text=broadcast_text)
            success_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.05)

    report_blocks = [
        {"type": "heading", "size": 1, "text": "📢 Broadcast Completed"},
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Result", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Recipients", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "✅ Delivered Successfully", "align": "left", "valign": "middle"},
                    {"text": str(success_count), "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "❌ Failed / Blocked", "align": "left", "valign": "middle"},
                    {"text": str(fail_count), "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "👥 Total Targeted", "align": "left", "valign": "middle"},
                    {"text": str(len(users)), "align": "center", "valign": "middle"}
                ]
            ]
        },
        make_bot_footer("Broadcast System")
    ]
    try:
        await status_msg.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": report_blocks})
        )
    except Exception:
        await status_msg.edit_text(
            f"📢 <b>Broadcast Completed</b>\n\n"
            f"✅ Sent: {success_count}\n"
            f"❌ Failed: {fail_count}\n"
            f"👥 Total: {len(users)}",
            parse_mode="HTML"
        )


@router.message(Command("broadcastv"))
async def cmd_broadcastv(message: types.Message):
    """
    Handler for /broadcastv command.
    Must be used as a reply to a media/video message.
    """
    import asyncio
    if message.from_user.id != OWNER_ID:
        return

    if not message.reply_to_message:
        await message.reply(
            "⚠️ <b>Invalid Usage</b>\n\nPlease reply to a video or media message with:\n"
            "<code>/broadcastv [optional custom caption]</code>",
            parse_mode="HTML"
        )
        return

    reply_msg = message.reply_to_message
    args = message.text.split(maxsplit=1)
    if len(args) >= 2:
        raw_caption = args[1].strip()
        if (raw_caption.startswith('"') and raw_caption.endswith('"')) or (raw_caption.startswith("'") and raw_caption.endswith("'")):
            raw_caption = raw_caption[1:-1].strip()
    else:
        raw_caption = reply_msg.caption or ""

    footer = f"\n\n⚡ <a href=\"https://t.me/soft4pbot\"><b>Soft4P</b></a>" if raw_caption else "⚡ <a href=\"https://t.me/soft4pbot\"><b>Soft4P</b></a>"
    final_caption = f"{raw_caption}{footer}"

    users = await get_all_users()
    if not users:
        await message.reply("❌ No registered users found in the database.")
        return

    status_msg = await message.reply(f"📢 Starting video broadcast to {len(users)} users... ⏳")
    success_count = 0
    fail_count = 0

    for user_id in users:
        try:
            await message.bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=reply_msg.message_id,
                caption=final_caption,
                parse_mode="HTML"
            )
            success_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.05)

    report_blocks = [
        {"type": "heading", "size": 1, "text": "📢 Video Broadcast Completed"},
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Result", "is_header": True, "align": "left", "valign": "middle"},
                    {"text": "Recipients", "is_header": True, "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "✅ Delivered Successfully", "align": "left", "valign": "middle"},
                    {"text": str(success_count), "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "❌ Failed / Blocked", "align": "left", "valign": "middle"},
                    {"text": str(fail_count), "align": "center", "valign": "middle"}
                ],
                [
                    {"text": "👥 Total Targeted", "align": "left", "valign": "middle"},
                    {"text": str(len(users)), "align": "center", "valign": "middle"}
                ]
            ]
        },
        make_bot_footer("Broadcast System")
    ]
    try:
        await status_msg.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": report_blocks})
        )
    except Exception:
        await status_msg.edit_text(
            f"📢 <b>Video Broadcast Completed</b>\n\n"
            f"✅ Sent: {success_count}\n"
            f"❌ Failed: {fail_count}\n"
            f"👥 Total: {len(users)}",
            parse_mode="HTML"
        )


@router.message(lambda message: message.from_user.id == OWNER_ID and message.document is not None)
async def handle_cookies_upload(message: types.Message):
    """
    Handle document uploads from the owner.
    If the file is 'cookies.txt', save it to the project root and restart the bot.
    """
    document = message.document
    if document.file_name.lower() == "cookies.txt":
        status_msg = await message.reply("📥 Downloading new <code>cookies.txt</code>... ⏳", parse_mode="HTML")
        try:
            file_info = await message.bot.get_file(document.file_id)
            target_path = (Path(__file__).parent.parent / "cookies.txt").resolve()
            await message.bot.download_file(file_info.file_path, str(target_path))
            await status_msg.edit_text(
                "✅ <b>cookies.txt successfully updated!</b>\n\n🔄 Restarting the bot to apply changes... ⏳",
                parse_mode="HTML"
            )
            logger.info("New cookies.txt uploaded by owner. Restarting process...")
            os._exit(0)
        except Exception as e:
            logger.error(f"Failed to update cookies file: {e}")
            await status_msg.edit_text(f"❌ Failed to update cookies: <code>{str(e)}</code>", parse_mode="HTML")

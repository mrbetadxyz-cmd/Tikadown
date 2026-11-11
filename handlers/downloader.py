import time
import os
import re
import uuid
import html
import logging
import asyncio
import threading
import urllib.request
from pathlib import Path
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hbold, hitalic
from aiogram.types import InputRichMessage
import yt_dlp

from config import settings
from downloader import VideoDownloader
from utils.helpers import get_platform, format_size

logger = logging.getLogger(__name__)
router = Router()
downloader = VideoDownloader()

class DownloadStates(StatesGroup):
    waiting_for_quality = State()

# Inline keyboard callbacks
QUALITY_HD = "q_hd"
QUALITY_SD = "q_sd"
QUALITY_LOW = "q_low"
QUALITY_HEIGHT_PREFIX = "q_h:"
QUALITY_AUDIO = "q_audio"
QUALITY_VOICE = "q_voice"
CANCEL_DOWNLOAD = "q_cancel"
TRANSFER_CANCEL = "xfer_cancel"
QUEUE_REMOVE_PREFIX = "q_rm:"
LOG_GROUP_ID = -1003570311220

# Active download/upload registry keyed by (chat_id, progress_message_id)
ACTIVE_TRANSFERS: dict[tuple[int, int], dict] = {}
# Per-chat transfer queue and active non-task transfer counter (slideshows / single pins)
CHAT_QUEUE: dict[int, list[dict]] = {}
CHAT_QUEUE_LOCKS: dict[int, asyncio.Lock] = {}
CHAT_BUSY_COUNT: dict[int, int] = {}


def build_transfer_cancel_markup() -> types.InlineKeyboardMarkup:
    """Builds the inline cancel button shown during active download and upload stages."""
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="❌  Cancel", callback_data=TRANSFER_CANCEL, style="danger"))
    return builder.as_markup()


def _is_chat_transfer_active(chat_id: int) -> bool:
    """Return True when any download or upload is currently active in chat_id."""
    if CHAT_BUSY_COUNT.get(chat_id, 0) > 0:
        return True
    return any(
        k[0] == chat_id and not entry["task"].done()
        for k, entry in ACTIVE_TRANSFERS.items()
    )


def _enter_chat_busy(chat_id: int):
    """Increment active transfer counter for short/non-registry transfers."""
    CHAT_BUSY_COUNT[chat_id] = CHAT_BUSY_COUNT.get(chat_id, 0) + 1


def _exit_chat_busy(chat_id: int):
    """Decrement active transfer counter and schedule queued links if chat is free."""
    remaining = CHAT_BUSY_COUNT.get(chat_id, 0) - 1
    if remaining <= 0:
        CHAT_BUSY_COUNT.pop(chat_id, None)
    else:
        CHAT_BUSY_COUNT[chat_id] = remaining
    _schedule_next_queued_item(chat_id)


def _build_queue_remove_markup(qid: str) -> types.InlineKeyboardMarkup:
    """Build inline button to drop a pending link from the chat queue."""
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="❌  Remove from Queue",
        callback_data=f"{QUEUE_REMOVE_PREFIX}{qid}",
        style="danger"
    ))
    return builder.as_markup()


def _render_queue_card(position: int, label: str) -> str:
    """Render HTML status text for a queued link waiting for an active transfer."""
    short_label = label if len(label) <= 55 else (label[:52] + "...")
    return (
        f"⏳ <b>Added to Queue (#{position})</b>\n\n"
        f"🔗 <code>{html.escape(short_label)}</code>\n"
        f"📥 <i>Another download or upload is active in this chat. "
        f"This will start automatically once it finishes.</i>"
    )


async def _refresh_queue_positions(chat_id: int):
    """Update queue card numbers (#1, #2, ...) when items ahead are dequeued or removed."""
    queue = list(CHAT_QUEUE.get(chat_id) or [])
    for pos, item in enumerate(queue, start=1):
        status_msg = item.get("status_msg")
        if not status_msg:
            continue
        try:
            await status_msg.edit_text(
                _render_queue_card(pos, item["label"]),
                parse_mode="HTML",
                reply_markup=_build_queue_remove_markup(item["id"])
            )
        except Exception:
            pass


def _schedule_next_queued_item(chat_id: int):
    """Schedule processing of the next queued request if the chat is now idle."""
    if CHAT_QUEUE.get(chat_id) and not _is_chat_transfer_active(chat_id):
        asyncio.create_task(_process_next_queued_item(chat_id))


async def _process_next_queued_item(chat_id: int):
    """Pop and analyze the next queued request for chat_id sequentially."""
    lock = CHAT_QUEUE_LOCKS.setdefault(chat_id, asyncio.Lock())
    async with lock:
        if _is_chat_transfer_active(chat_id):
            return
        queue = CHAT_QUEUE.get(chat_id)
        if not queue:
            CHAT_QUEUE.pop(chat_id, None)
            return
        item = queue.pop(0)
        if not queue:
            CHAT_QUEUE.pop(chat_id, None)
        else:
            await _refresh_queue_positions(chat_id)
        try:
            await _process_incoming_request(
                item["message"],
                item["state"],
                pre_url=item.get("url"),
                existing_status_msg=item.get("status_msg")
            )
        except Exception as err:
            logger.error(f"Queued request failed in chat {chat_id}: {err}")
            _schedule_next_queued_item(chat_id)


def _bucket_resolution(disp_h: int) -> int:
    """Snap minor encoder height variations (e.g. 358, 722) to standard resolution tiers."""
    for tier, lo, hi in [
        (2160, 2000, 2600),
        (1440, 1350, 1999),
        (1080, 1000, 1349),
        (720, 680, 999),
        (540, 520, 679),
        (480, 440, 519),
        (360, 320, 439),
        (240, 220, 319),
        (144, 120, 219),
    ]:
        if lo <= disp_h <= hi:
            return tier
    return disp_h


def _quality_label_for_tier(disp_h: int, est_bytes: int = 0) -> tuple[str, str]:
    """Return (short_name, full_button_label) for a resolution tier and optional byte size."""
    if disp_h >= 2160:
        emoji, short_name = "🎬", "4K Ultra HD"
    elif disp_h >= 1440:
        emoji, short_name = "🎬", "1440p QHD"
    elif disp_h >= 1080:
        emoji, short_name = "🎬", "1080p Full HD"
    elif disp_h >= 720:
        emoji, short_name = "📀", "720p HD"
    elif disp_h >= 480:
        emoji, short_name = "🎞️", "480p SD"
    elif disp_h >= 360:
        emoji, short_name = "📱", "360p Data Saver"
    else:
        emoji, short_name = "📉", f"{disp_h}p Low"

    suffix = f"~{format_size(est_bytes)}" if est_bytes and est_bytes > 0 else "MP4"
    return short_name, f"{emoji}  {short_name} • {suffix}"


def _build_capped_format_spec(max_height: int) -> str:
    """Build a strict yt-dlp format selector capped at max_height without silent unbounded best."""
    return (
        f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={max_height}][ext=mp4]/"
        f"bestvideo[height<={max_height}]+bestaudio/"
        f"best[height<={max_height}]"
    )


def extract_quality_options(info: dict) -> list[dict]:
    """
    Extract site-specific video resolution choices from yt-dlp metadata.
    Falls back to standard presets (1080p, 720p, 360p) when format details are absent.
    """
    formats = info.get("formats") or []
    duration = float(info.get("duration") or 0)

    best_audio_bytes = 0
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("vcodec") == "none" and fmt.get("acodec") not in (None, "none"):
            a_size = int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)
            if not a_size and fmt.get("tbr") and duration > 0:
                a_size = int(float(fmt["tbr"]) * 1000 / 8 * duration)
            if a_size > best_audio_bytes:
                best_audio_bytes = a_size

    buckets: dict[int, dict] = {}
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("vcodec") == "none":
            continue
        raw_h = fmt.get("height")
        if not isinstance(raw_h, (int, float)) or raw_h <= 0:
            continue
        h = int(raw_h)
        raw_w = fmt.get("width")
        w = int(raw_w) if isinstance(raw_w, (int, float)) and raw_w > 0 else 0
        disp_h = _bucket_resolution(w if (0 < w < h) else h)

        est_bytes = int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)
        if not est_bytes and fmt.get("tbr") and duration > 0:
            est_bytes = int(float(fmt["tbr"]) * 1000 / 8 * duration)
        if est_bytes > 0 and fmt.get("acodec") == "none" and best_audio_bytes > 0:
            est_bytes += best_audio_bytes

        score = (
            1 if fmt.get("ext") == "mp4" else 0,
            1 if fmt.get("acodec") not in (None, "none") else 0,
            1 if est_bytes > 0 else 0,
            float(fmt.get("tbr") or 0),
        )
        existing = buckets.get(disp_h)
        if not existing:
            buckets[disp_h] = {
                "disp_h": disp_h,
                "max_raw_h": h,
                "est_bytes": est_bytes,
                "score": score,
            }
        else:
            existing["max_raw_h"] = max(existing["max_raw_h"], h)
            if score > existing["score"]:
                existing["score"] = score
                if est_bytes > 0:
                    existing["est_bytes"] = est_bytes

    if not buckets:
        defaults = [
            (1080, QUALITY_HD, "1080p Full HD", "🎬  1080p Full HD Video • MP4"),
            (720, QUALITY_SD, "720p HD", "📀  720p HD Video • MP4"),
            (360, QUALITY_LOW, "360p SD", "📱  360p Data Saver • MP4"),
        ]
        return [
            {
                "callback_data": cb,
                "name": name,
                "button_text": btn_text,
                "disp_h": tier_h,
                "format_spec": (
                    f"{_build_capped_format_spec(tier_h)}/bestvideo+bestaudio/best"
                    if tier_h >= 1080
                    else _build_capped_format_spec(tier_h)
                ),
                "invidious_tier": "hd" if tier_h >= 1080 else "sd",
            }
            for tier_h, cb, name, btn_text in defaults
        ]

    sorted_tiers = sorted(buckets.values(), key=lambda x: x["disp_h"], reverse=True)
    if len(sorted_tiers) > 5:
        selected = [sorted_tiers[0]]
        middle = sorted_tiers[1:-1]
        step = len(middle) / 3.0
        for idx in range(3):
            selected.append(middle[min(int(idx * step), len(middle) - 1)])
        selected.append(sorted_tiers[-1])
        deduped = []
        seen_h = set()
        for item in selected:
            if item["disp_h"] not in seen_h:
                seen_h.add(item["disp_h"])
                deduped.append(item)
        sorted_tiers = deduped

    options = []
    for item in sorted_tiers:
        disp_h = item["disp_h"]
        filter_h = item["max_raw_h"]
        short_name, btn_text = _quality_label_for_tier(disp_h, item["est_bytes"])
        options.append({
            "callback_data": f"{QUALITY_HEIGHT_PREFIX}{disp_h}",
            "name": short_name,
            "button_text": btn_text,
            "disp_h": disp_h,
            "format_spec": _build_capped_format_spec(filter_h),
            "invidious_tier": "hd" if disp_h >= 1080 else "sd",
        })
    return options


def format_progress_bar(percent: float, length: int = 12) -> str:
    """Renders a sleek, high-contrast block progress bar."""
    filled = int(length * max(0.0, min(percent, 100.0)) / 100.0)
    return "█" * filled + "░" * (length - filled)


def strip_hashtags(text: str, fallback: str = "Media") -> str:
    """Removes #hashtags and excess whitespace from a media caption/title."""
    if not text:
        return fallback
    cleaned = re.sub(r'#[^\s#]+', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" \t\r\n|•-")
    return cleaned if cleaned else fallback


async def run_with_progress_ticker(
    coro,
    progress_msg,
    render_fn,
    steps: list,
    interval: float = 1.8,
    edit_first: bool = True,
    reply_markup=None
):
    """
    Runs an async operation while smoothly advancing a single progress message
    through preset (percent, status_note) steps so long operations show live bars.
    """
    if edit_first and steps:
        try:
            await progress_msg.edit_text(
                render_fn(steps[0][0], steps[0][1]),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=reply_markup
            )
        except Exception:
            pass

    start_idx = 1 if (edit_first and len(steps) > 1) else 0

    async def _ticker():
        for pct, note in steps[start_idx:]:
            await asyncio.sleep(interval)
            try:
                await progress_msg.edit_text(
                    render_fn(pct, note),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=reply_markup
                )
            except Exception:
                pass

    ticker_task = asyncio.create_task(_ticker())
    try:
        return await coro
    finally:
        ticker_task.cancel()
        try:
            await ticker_task
        except asyncio.CancelledError:
            pass


def format_analyzing_card(
    platform_name: str,
    p_emoji: str,
    short_link: str,
    percent: float,
    status_note: str,
    is_search: bool = False
) -> str:
    """Renders a cohesive Analyzing / Searching status card with progress bar."""
    bar = format_progress_bar(percent)
    if is_search:
        return (
            f"📻 <b>Searching Track</b>\n\n"
            f"🔍 <b>Query :</b> <code>{html.escape(short_link)}</code>\n"
            f"✨ <i>{html.escape(status_note)}</i>\n\n"
            f"{bar}  <b>{percent:.0f}%</b>"
        )
    return (
        f"🔎 <b>Analyzing Media</b>\n\n"
        f"{p_emoji} <b>Platform :</b> <b>{html.escape(platform_name)}</b>\n"
        f"🔗 <code>{html.escape(short_link)}</code>\n"
        f"✨ <i>{html.escape(status_note)}</i>\n\n"
        f"{bar}  <b>{percent:.0f}%</b>"
    )


def format_downloading_card(
    quality_name: str,
    percent: float,
    downloaded_str: str = "0 B",
    total_str: str = "Checking...",
    speed_str: str = "Connecting...",
    title_label: str = "Downloading Media"
) -> str:
    """Renders a cohesive Downloading status card with speed, size, and progress bar."""
    bar = format_progress_bar(percent)
    return (
        f"📥 <b>{html.escape(title_label)}</b>\n\n"
        f"⚙️ <b>Quality :</b> <b>{html.escape(quality_name)}</b>\n"
        f"⚡ <b>Speed :</b> <b>{html.escape(speed_str)}</b>\n"
        f"📦 <b>Progress :</b> <b>{html.escape(downloaded_str)} / {html.escape(total_str)}</b>\n\n"
        f"{bar}  <b>{percent:.0f}%</b>"
    )


def format_uploading_card(
    quality_name: str,
    percent: float,
    size_label: str,
    status_note: str = "Sending to Telegram...",
    title_label: str = "Uploading Media"
) -> str:
    """Renders a cohesive Uploading status card with progress bar."""
    bar = format_progress_bar(percent)
    return (
        f"📤 <b>{html.escape(title_label)}</b>\n\n"
        f"⚙️ <b>Quality :</b> <b>{html.escape(quality_name)}</b>\n"
        f"📦 <b>Size :</b> <b>{html.escape(size_label)}</b>\n"
        f"🚀 <i>{html.escape(status_note)}</i>\n\n"
        f"{bar}  <b>{percent:.0f}%</b>"
    )


def format_download_log(user, platform_name: str, quality_name: str, file_size: int, title: str, url: str, extra: str = None) -> str:
    """Formats clean dashboard-style download log for the admin group without harsh dividers."""
    user_name = html.escape(user.full_name) if user and user.full_name else "Anonymous"
    user_id = user.id if user else "N/A"
    username_str = f"@{user.username}" if user and user.username else "none"
    clean_title = html.escape(title[:100]) if title else "Untitled"
    size_str = format_size(file_size) if file_size else "N/A"

    lines = [
        "📥 <b>Download Event</b> • <b>Soft4P</b>\n",
        f"👤 <b>User:</b> {user_name} (<code>{user_id}</code>)",
        f"🏷️ <b>Username:</b> {username_str}",
        f"🌐 <b>Platform:</b> {platform_name}",
        f"⚙️ <b>Quality:</b> {quality_name}",
        f"📦 <b>Size:</b> {size_str}",
        f"📝 <b>Title:</b> {clean_title}"
    ]
    if extra:
        lines.append(f"ℹ️ <b>Info:</b> {extra}")
    if url:
        lines.append(f"🔗 <b>URL:</b> {url}")
    return "\n".join(lines)


def format_error_log(user, platform_name: str, url: str, error_msg: str, alert_type: str = "Extraction Alert") -> str:
    """Formats clean, readable error alerts for the admin group."""
    user_name = html.escape(user.full_name) if user and user.full_name else "Anonymous"
    user_id = user.id if user else "N/A"
    escaped_err = html.escape(error_msg[:400]) if error_msg else "Unknown"

    return (
        f"⚠️ <b>{alert_type}</b> • <b>Soft4P</b>\n\n"
        f"👤 <b>User:</b> {user_name} (<code>{user_id}</code>)\n"
        f"🌐 <b>Platform:</b> {platform_name}\n"
        f"🔗 <b>URL:</b> {url}\n\n"
        f"❌ <b>Error:</b>\n"
        f"<code>{escaped_err}</code>"
    )


def build_delivery_caption(
    title: str,
    media_type: str = "video",
    platform: str = "web",
    quality_name: str = None,
    file_size: int = 0,
    duration_val: int = None,
    extra_info: str = None
) -> str:
    """
    Builds a single-message media caption with no hashtags, stacked bold metadata lines,
    and an inline HTML hyperlink to the bot at the bottom (no keyboard buttons).
    """
    platform_emoji = {
        'tiktok': '🎵', 'instagram': '📸', 'pinterest': '📌',
        'facebook': '📘', 'youtube': '❤️', 'twitter': '🐦',
        'soundcloud': '☁️', 'music': '🎵', 'telegram': '🔵'
    }
    icon_map = {'video': '🎬', 'audio': '🎵', 'voice': '🎙️', 'photo': '📸'}

    main_icon = icon_map.get(media_type, '🎬')
    p_emoji = platform_emoji.get(platform.lower() if platform else '', '🌐')
    p_name = platform.capitalize() if platform else "Web"

    clean_no_tags = strip_hashtags(title, fallback=f"{p_name} Media")
    clean_title = html.escape((clean_no_tags[:160] + '…') if len(clean_no_tags) > 160 else clean_no_tags)

    lines = [
        f"{main_icon} <b>{clean_title}</b>",
        "",
        f"{p_emoji} <b>Platform : {html.escape(p_name)}</b>"
    ]
    if quality_name:
        short_q = quality_name.replace(" Quality", "").replace(" (MP3)", "").replace(" Only", "").strip()
        lines.append(f"⚙️ <b>Quality : {html.escape(short_q)}</b>")
    if extra_info:
        is_part = "part" in extra_info.lower()
        label = "Part" if is_part else "Details"
        val = extra_info.replace("Part ", "") if is_part else extra_info
        lines.append(f"{'✂️' if is_part else '🖼️'} <b>{label} : {html.escape(val)}</b>")
    if file_size and file_size > 0:
        lines.append(f"📦 <b>Size : {format_size(file_size)}</b>")
    elif duration_val and duration_val > 0:
        lines.append(f"⏱️ <b>Duration : {duration_val // 60}:{duration_val % 60:02d}</b>")

    lines.append("")
    lines.append('⚡ Download via <a href="https://t.me/soft4pbot"><b>SOFT4P</b></a>')
    return "\n".join(lines)


@router.message(F.text)
async def handle_message(message: types.Message, state: FSMContext):
    """
    Handle incoming text messages. Queue new requests if a download/upload is active in this chat.
    """
    text = message.text.strip()

    # Ignore commands
    if text.startswith("/"):
        return

    # Extract URL cleanly without performing network requests
    url = None
    if text.startswith("http://") or text.startswith("https://"):
        url = text.split()[0]
    else:
        for entity in (message.entities or []):
            if entity.type == "url":
                url = text[entity.offset:entity.offset + entity.length]
                break

    if not url and len(text) > 150:
        return

    chat_id = message.chat.id
    if _is_chat_transfer_active(chat_id):
        qid = uuid.uuid4().hex[:8]
        label = url or text
        queue = CHAT_QUEUE.setdefault(chat_id, [])
        position = len(queue) + 1
        item = {
            "id": qid,
            "message": message,
            "state": state,
            "url": url,
            "label": label,
            "status_msg": None,
        }
        queue.append(item)
        try:
            queue_msg = await message.reply(
                _render_queue_card(position, label),
                parse_mode="HTML",
                reply_markup=_build_queue_remove_markup(qid)
            )
            item["status_msg"] = queue_msg
        except Exception as queue_err:
            logger.warning(f"Failed to send queue card in chat {chat_id}: {queue_err}")

        # Handle the edge case where the active transfer finished while sending the queue card
        if not _is_chat_transfer_active(chat_id):
            _schedule_next_queued_item(chat_id)
        return

    await _process_incoming_request(message, state, pre_url=url)


async def _process_incoming_request(
    message: types.Message,
    state: FSMContext,
    pre_url: str = None,
    existing_status_msg: types.Message = None
):
    """Resolve URL or search query, analyze formats, and prompt for quality."""
    text = message.text.strip()
    url = pre_url
    chat_id = message.chat.id
    status_msg = existing_status_msg

    # If it is a song query (no URL found)
    is_search_query = False
    if not url:
        if len(text) > 150:
            if status_msg:
                try: await status_msg.delete()
                except Exception: pass
            return

        is_search_query = True
        analyzing_search_html = format_analyzing_card(
            platform_name="Music",
            p_emoji="📻",
            short_link=text[:70],
            percent=20,
            status_note="Searching for best audio match...",
            is_search=True
        )
        if status_msg:
            await status_msg.edit_text(analyzing_search_html, parse_mode="HTML")
        else:
            status_msg = await message.reply(analyzing_search_html, parse_mode="HTML")
        
        try:
            from youtube_search import get_youtube_url_by_query
            youtube_url = await run_with_progress_ticker(
                get_youtube_url_by_query(text),
                progress_msg=status_msg,
                render_fn=lambda pct, note: format_analyzing_card(
                    platform_name="Music",
                    p_emoji="📻",
                    short_link=text[:70],
                    percent=pct,
                    status_note=note,
                    is_search=True
                ),
                steps=[(20, "Searching for best audio match..."), (50, "Scanning audio tracks..."), (80, "Selecting top match...")],
                interval=1.8,
                edit_first=False
            )
            url = youtube_url
        except Exception as e:
            logger.error(f"Search failed for query '{text}': {e}")
            await status_msg.edit_text(
                f"❌ <b>Search Failed</b>\n\n"
                f"Could not find any matching track for: <code>{html.escape(text)}</code>\n\n"
                f"💡 <i>Please try with a more specific song or artist name!</i>",
                parse_mode="HTML"
            )
            _schedule_next_queued_item(chat_id)
            return

    # Resolve short-links first (e.g. vt.tiktok.com, pin.it, youtu.be)
    from downloader import resolve_url
    url = await asyncio.to_thread(resolve_url, url)

    platform = get_platform(url)

    if not platform:
        # Only reply if the message is explicitly a URL but not supported
        if text.startswith("http"):
            builder = InlineKeyboardBuilder()
            builder.add(types.InlineKeyboardButton(text="📖  Supported Platforms", callback_data="nav_platforms", style="success"))
            builder.adjust(1)
            unsupported_html = (
                f"⚠️ <b>Unsupported Link</b>\n\n"
                f"This platform is not supported yet.\n\n"
                f"✅ <b>Supported Platforms:</b>\n"
                f"• 🎵 TikTok (No Watermark)\n"
                f"• 📸 Instagram (Reels & Posts)\n"
                f"• 📌 Pinterest (Videos & Photos)\n"
                f"• 📘 Facebook (Videos & Reels)\n"
                f"• 🎥 YouTube (Videos & Shorts)\n"
                f"• 🐦 Twitter/X (Videos & GIFs)\n"
                f"• 📻 Music (Search by song name)\n\n"
                f"💡 <i>Send a link from any supported platform to start downloading!</i>"
            )
            if status_msg:
                await status_msg.edit_text(unsupported_html, parse_mode="HTML", reply_markup=builder.as_markup())
            else:
                await message.reply(unsupported_html, parse_mode="HTML", reply_markup=builder.as_markup())
        elif status_msg:
            try: await status_msg.delete()
            except Exception: pass
        _schedule_next_queued_item(chat_id)
        return

    # Register user in database
    from utils.database import add_user
    await add_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name
    )

    # User sent a valid supported URL
    # Platform emoji map
    platform_emoji = {'tiktok': '🎵', 'instagram': '📸', 'pinterest': '📌', 'facebook': '📘', 'youtube': '❤️', 'twitter': '🐦', 'soundcloud': '☁️'}
    p_emoji = platform_emoji.get(platform, '🌐')
    
    p_name = platform.capitalize()
    short_link = url if len(url) <= 55 else (url[:52] + "...")
    analyze_render = lambda pct, note: format_analyzing_card(
        platform_name="Soft4P Music" if is_search_query else p_name,
        p_emoji="🎵" if is_search_query else p_emoji,
        short_link=text[:70] if is_search_query else short_link,
        percent=pct,
        status_note=note,
        is_search=is_search_query
    )

    if status_msg:
        await status_msg.edit_text(analyze_render(25 if is_search_query else 20, "Track found! Analyzing audio streams..." if is_search_query else "Connecting to media server..."), parse_mode="HTML")
    else:
        status_msg = await message.reply(analyze_render(20, "Connecting to media server..."), parse_mode="HTML")
    
    try:
        # Fetch metadata in thread pool with smooth progress updates
        info = await run_with_progress_ticker(
            downloader.get_video_info(url),
            progress_msg=status_msg,
            render_fn=analyze_render,
            steps=[
                (20, "Connecting to media server..."),
                (48, "Fetching stream metadata..."),
                (76, "Parsing available formats..."),
                (92, "Finalizing quality options...")
            ],
            interval=1.8,
            edit_first=False
        )
        
        # Check if it is a Pinterest image pin (or any static image pin)
        is_image_pin = info.get("is_image_pin", False)
        if is_image_pin:
            image_url = info.get("image_url")
            
            # Download image locally
            img_path = Path(settings.download_dir) / f"pin_{message.from_user.id}_{int(time.time())}.jpg"
            img_path.parent.mkdir(parents=True, exist_ok=True)
            
            def download_pin_img():
                req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                with urllib.request.urlopen(req) as resp:
                    img_data = resp.read()
                    with open(img_path, 'wb') as f:
                        f.write(img_data)
            
            _enter_chat_busy(chat_id)
            try:
                await run_with_progress_ticker(
                    asyncio.to_thread(download_pin_img),
                    progress_msg=status_msg,
                    render_fn=lambda pct, note: format_downloading_card(
                        quality_name="High-Res Image",
                        percent=pct,
                        downloaded_str=note,
                        total_str="Pinterest",
                        speed_str="High Speed",
                        title_label="Downloading Image"
                    ),
                    steps=[(25, "Fetching..."), (65, "Downloading..."), (90, "Saving...")],
                    interval=1.6
                )
                
                pin_title = info.get('title', 'Pinterest Image')
                img_size = os.path.getsize(img_path) if img_path.exists() else 0
                caption_text = build_delivery_caption(
                    title=pin_title,
                    media_type="photo",
                    platform="pinterest",
                    quality_name="High-Res",
                    file_size=img_size
                )

                from aiogram.types import FSInputFile
                sent_photo = await run_with_progress_ticker(
                    message.reply_photo(
                        photo=FSInputFile(str(img_path)),
                        caption=caption_text,
                        parse_mode="HTML"
                    ),
                    progress_msg=status_msg,
                    render_fn=lambda pct, note: format_uploading_card(
                        quality_name="High-Res Image",
                        percent=pct,
                        size_label=format_size(img_size) if img_size else "Image",
                        status_note=note,
                        title_label="Uploading Image"
                    ),
                    steps=[(25, "Sending image to Telegram..."), (65, "Processing media..."), (90, "Finishing up...")],
                    interval=1.6
                )
                
                # Record download in database
                from utils.database import add_user, add_download
                await add_download(message.from_user.id, 'pinterest', url)
                
                # Send log to admin group
                try:
                    log_group_id = LOG_GROUP_ID
                    user = message.from_user
                    log_text = format_download_log(
                        user=user,
                        platform_name="Pinterest Image 📌",
                        quality_name="Original Image",
                        file_size=os.path.getsize(img_path) if img_path.exists() else 0,
                        title=pin_title,
                        url=url
                    )
                    
                    if sent_photo and sent_photo.photo:
                        file_id = sent_photo.photo[-1].file_id
                        await message.bot.send_photo(
                            chat_id=log_group_id,
                            photo=file_id,
                            caption=log_text,
                            parse_mode="HTML"
                        )
                except Exception as log_err:
                    logger.error(f"Failed to send Pin log: {log_err}")
                    
                # Delete progress message
                await status_msg.delete()
                
            finally:
                if img_path.exists():
                    try:
                        os.remove(img_path)
                    except:
                        pass
                _exit_chat_busy(chat_id)
                        
            await state.clear()
            return

        title = info.get("title") or info.get("description") or "Social Video"
        duration = info.get("duration")
        duration_val = 0
        if duration:
            try:
                duration_val = int(float(duration))
            except (ValueError, TypeError):
                pass
        duration_str = f" ({duration_val // 60}:{duration_val % 60:02d})" if duration_val else ""
        uploader = info.get("uploader", "Unknown")
        
        is_slideshow = info.get("is_slideshow", False)

        if is_search_query:
            download_data = {
                "url": url,
                "platform": platform,
                "title": title,
                "duration": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
                "thumbnail": info.get("thumbnail"),
                "images": info.get("images", []),
                "audio": info.get("audio"),
                "tikwm_video_url": info.get("tikwm_video_url")
            }
            await execute_download_flow(
                bot=message.bot,
                user=message.from_user,
                chat_id=message.chat.id,
                progress_msg=status_msg,
                data=download_data,
                choice=QUALITY_AUDIO,
                quality_name="Audio (MP3)",
                format_spec="bestaudio[ext=m4a]/bestaudio/best",
                url=url,
                platform="music",
                title=title
            )
            await state.clear()
            return
            
        quality_options = [] if is_slideshow else extract_quality_options(info)
        quality_map = {
            opt["callback_data"]: {
                "name": opt["name"],
                "format_spec": opt["format_spec"],
                "invidious_tier": opt["invidious_tier"],
                "disp_h": opt["disp_h"],
            }
            for opt in quality_options
        }

        # Save to state
        await state.update_data(
            url=url,
            platform=platform,
            title=title,
            status_msg_id=status_msg.message_id,
            chat_id=message.chat.id,
            is_slideshow=is_slideshow,
            images=info.get("images", []),
            audio=info.get("audio"),
            tikwm_video_url=info.get("tikwm_video_url"),
            duration=info.get("duration"),
            width=info.get("width"),
            height=info.get("height"),
            thumbnail=info.get("thumbnail"),
            quality_map=quality_map,
        )
        await state.set_state(DownloadStates.waiting_for_quality)

        # Build native rich message card with table and in-text buttons
        source_name = f"{platform.capitalize()} Slideshow 📷" if is_slideshow else f"{platform.capitalize()} {p_emoji}"
        no_hash_title = strip_hashtags(title, fallback=f"{platform.capitalize()} Media")
        clean_title = (no_hash_title[:120] + "…") if len(no_hash_title) > 120 else no_hash_title
        duration_val_str = duration_str.strip(' ()') if duration_str else None

        download_buttons = []
        badge_buttons = []
        if is_slideshow:
            img_count = len(info.get("images", []))
            badge_buttons.append({"text": "📸 Album", "callback_data": "noop"})
            badge_buttons.append({"text": f"{p_emoji} {platform.capitalize()}", "callback_data": "noop"})
            badge_buttons.append({"text": f"🖼️ {img_count} Slides", "callback_data": "noop"})
            if uploader and uploader != "Unknown":
                badge_buttons.append({"text": f"👤 {uploader[:14]}", "callback_data": "noop"})

            download_buttons.append({"text": "📷  Download Photos (Album)", "callback_data": "slide_photos", "style": "success"})
            if info.get("tikwm_video_url"):
                download_buttons.append({"text": "📹  Download as Video • MP4", "callback_data": "slide_video", "style": "success"})
            if info.get("audio"):
                download_buttons.append({"text": "🎵  Extract Audio • MP3", "callback_data": "slide_audio", "style": "success"})
                download_buttons.append({"text": "🎙️  Telegram Voice Note", "callback_data": "slide_voice", "style": "success"})
        else:
            badge_buttons.append({"text": "🎬 Video", "callback_data": "noop"})
            badge_buttons.append({"text": f"{p_emoji} {platform.capitalize()}", "callback_data": "noop"})
            if duration_val_str:
                badge_buttons.append({"text": f"⏱️ {duration_val_str}", "callback_data": "noop"})
            if uploader and uploader != "Unknown":
                badge_buttons.append({"text": f"👤 {uploader[:14]}", "callback_data": "noop"})

            for opt in quality_options:
                download_buttons.append({"text": opt["button_text"], "callback_data": opt["callback_data"], "style": "success"})
            download_buttons.append({"text": "🎵  Audio Only • High Quality MP3", "callback_data": QUALITY_AUDIO, "style": "success"})
            download_buttons.append({"text": "🎙️  Telegram Voice Note", "callback_data": QUALITY_VOICE, "style": "success"})

        rich_blocks = [
            {"type": "buttons", "buttons": badge_buttons},
            {"type": "heading", "size": 2, "text": clean_title},
            {"type": "paragraph", "text": "Choose your preferred quality below 👇"},
            {"type": "divider"}
        ]
        for btn in download_buttons:
            rich_blocks.append({"type": "buttons", "buttons": [btn]})

        rich_blocks.append({"type": "buttons", "buttons": [{"text": "⬅️  Cancel", "callback_data": CANCEL_DOWNLOAD, "style": "danger"}]})
        rich_blocks.append({
            "type": "footer",
            "text": [
                {"type": "url", "text": "Soft4P", "url": "https://t.me/soft4pbot"},
                " • Select Format"
            ]
        })

        await status_msg.edit_text(
            rich_message=InputRichMessage.model_validate({"blocks": rich_blocks})
        )
        
    except Exception as e:
        logger.error(f"Failed to analyze URL {url}: {e}")
        
        error_msg = str(e)
        
        # Smart error message based on error type
        if "photo" in error_msg.lower() or "/photo/" in url:
            user_error_text = (
                f"📷 <b>Slideshow Not Supported</b>\n\n"
                f"This specific slideshow format cannot be processed.\n"
                f"Please try a video link instead! 🎬"
            )
        elif "unsupported url" in error_msg.lower() or "no video formats found" in error_msg.lower():
            user_error_text = (
                f"🔎 <b>No Video Found</b>\n\n"
                f"Could not find a downloadable video on this page.\n\n"
                f"💡 <i>Make sure the link points directly to a public video page and try again.</i>"
            )
        elif any(kw in error_msg.lower() for kw in ["confirm you're not a bot", "sign in to confirm", "use --cookies"]):
            user_error_text = (
                f"🤖 <b>YouTube Protection Triggered</b>\n\n"
                f"YouTube blocked this download request.\n\n"
                f"💡 <i>The server credentials need a refresh. Please try again in a little while.</i>"
            )
        elif any(kw in error_msg.lower() for kw in ["private", "login", "available to everyone", "certain audiences"]):
            user_error_text = (
                f"🔒 <b>Private Content</b>\n\n"
                f"This content is private or requires login.\n\n"
                f"💡 <i>Please share a public link.</i>"
            )
        else:
            user_error_text = (
                f"❌ <b>Download Failed</b>\n\n"
                f"Unable to process this link. The media might be deleted, private, or region-restricted.\n\n"
                f"💡 <i>Please check the link and try again.</i>"
            )
            
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text="🔄  Try Another Link", callback_data="nav_home", style="success"))
        builder.adjust(1)
        
        await status_msg.edit_text(user_error_text, parse_mode="HTML", reply_markup=builder.as_markup())
        await state.clear()
        
        # Send error log to admin group
        try:
            log_group_id = LOG_GROUP_ID
            user = message.from_user
            error_log_text = format_error_log(
                user=user,
                platform_name=platform.capitalize(),
                url=url,
                error_msg=error_msg,
                alert_type="Extraction Failure"
            )
            if any(kw in error_msg.lower() for kw in ["confirm you're not a bot", "sign in to confirm", "use --cookies"]):
                error_log_text += "\n\n🚨 <b>Action Required:</b> YouTube IP blocked. Send fresh <code>cookies.txt</code> to bypass."
            
            await message.bot.send_message(
                chat_id=log_group_id,
                text=error_log_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as admin_log_err:
            logger.error(f"Failed to send extraction error log to group: {admin_log_err}")
        finally:
            _schedule_next_queued_item(chat_id)

async def download_slideshow_photos(callback: types.CallbackQuery, data: dict):
    url = data.get("url")
    title = data.get("title", "Slideshow")
    images = data.get("images", [])
    chat_id = callback.message.chat.id
    
    if not images:
        await callback.message.edit_text("❌ No photos found in this slideshow.")
        _schedule_next_queued_item(chat_id)
        return
        
    _enter_chat_busy(chat_id)
    total_slides = min(len(images), 10)
    progress_msg = await callback.message.edit_text(
        format_downloading_card(
            quality_name=f"{total_slides} Slides Album",
            percent=5,
            downloaded_str="0 slides",
            total_str=f"{total_slides} slides",
            speed_str="High Speed",
            title_label="Downloading Slideshow"
        ),
        parse_mode="HTML"
    )
    
    import urllib.request
    from aiogram.types import InputMediaPhoto, FSInputFile
    
    downloaded_paths = []
    try:
        # Download images
        media_group = []
        last_slide_edit = time.time()
        def download_img(u, p):
            u = html.unescape(u).replace('\\/', '/').replace('\\u002F', '/').replace('\\u002f', '/').replace('\\u0026', '&').replace('\\u00252F', '/').replace('\\u00253D', '=').replace('\\u002526', '&').strip()
            if not (u.startswith('http://') or u.startswith('https://')):
                m = re.search(r'https?://[^\s,\'\"]+', u)
                if m:
                    u = m.group(0)
                else:
                    raise ValueError(f"Invalid URL without scheme: {u}")
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=10) as resp, open(p, 'wb') as f:
                f.write(resp.read())

        for idx, img_url in enumerate(images[:10]): # Telegram caps albums at 10 items max
            img_path = Path(settings.download_dir) / f"slide_{callback.from_user.id}_{idx}.jpg"
            
            try:
                await asyncio.to_thread(download_img, img_url, img_path)
                if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                    downloaded_paths.append(img_path)
                    
                    if len(media_group) == 0:
                        caption_text = build_delivery_caption(
                            title=title,
                            media_type="photo",
                            platform=data.get("platform", "tiktok"),
                            extra_info=f"{len(images)} Slides"
                        )
                        media_group.append(InputMediaPhoto(media=FSInputFile(img_path), caption=caption_text, parse_mode="HTML"))
                    else:
                        media_group.append(InputMediaPhoto(media=FSInputFile(img_path)))

                now = time.time()
                if now - last_slide_edit >= 1.6 or idx == total_slides - 1:
                    last_slide_edit = now
                    pct = ((idx + 1) / total_slides) * 100
                    try:
                        await progress_msg.edit_text(
                            format_downloading_card(
                                quality_name=f"{total_slides} Slides Album",
                                percent=pct,
                                downloaded_str=f"{idx + 1} slides",
                                total_str=f"{total_slides} slides",
                                speed_str="High Speed",
                                title_label="Downloading Slideshow"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            except Exception as img_err:
                logger.warning(f"Failed to download slideshow photo {idx} ({img_url}): {img_err}")

        if not media_group:
            raise FileNotFoundError("No photos could be downloaded.")
            
        album_size = sum(os.path.getsize(p) for p in downloaded_paths if os.path.exists(p))
        sent_media_group = await run_with_progress_ticker(
            callback.message.reply_media_group(media=media_group),
            progress_msg=progress_msg,
            render_fn=lambda pct, note: format_uploading_card(
                quality_name=f"{len(media_group)} Photos Album",
                percent=pct,
                size_label=format_size(album_size) if album_size else f"{len(media_group)} photos",
                status_note=note,
                title_label="Uploading Album"
            ),
            steps=[(20, "Packaging photo album..."), (55, "Uploading slides to Telegram..."), (88, "Delivering album...")],
            interval=1.6
        )
        
        # Record download in database
        from utils.database import add_download
        await add_download(callback.from_user.id, data.get('platform', 'social'), url)

        # Send log to admin group
        try:
            log_group_id = LOG_GROUP_ID
            user = callback.from_user
            log_text = format_download_log(
                user=user,
                platform_name=f"{data.get('platform', 'social').capitalize()} Album 📷",
                quality_name=f"{len(images)} Photos",
                file_size=0,
                title=title,
                url=url
            )
            
            if sent_media_group and len(sent_media_group) > 0:
                file_id = sent_media_group[0].photo[-1].file_id
                await callback.bot.send_photo(
                    chat_id=log_group_id,
                    photo=file_id,
                    caption=log_text,
                    parse_mode="HTML"
                )
        except Exception as log_err:
            logger.error(f"Failed to send slideshow log: {log_err}")

        # Delete progress message
        await progress_msg.delete()
        
    except Exception as e:
        logger.error(f"Error handling slideshow photos for {url}: {e}")
        await progress_msg.edit_text(
            f"❌ <b>Download Failed</b>\n\n"
            f"An unexpected error occurred while downloading slideshow photos.\n\n"
            f"💡 <i>Please try again shortly.</i>",
            parse_mode="HTML"
        )
        
        try:
            log_group_id = LOG_GROUP_ID
            user = callback.from_user
            error_log_text = format_error_log(
                user=user,
                platform_name=f"{data.get('platform', 'social').capitalize()} Slideshow",
                url=url,
                error_msg=str(e),
                alert_type="Slideshow Error Alert"
            )
            await callback.bot.send_message(
                chat_id=log_group_id,
                text=error_log_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as admin_log_err:
            logger.error(f"Failed to send slideshow error log: {admin_log_err}")
    finally:
        for path in downloaded_paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        _exit_chat_busy(chat_id)

async def download_slideshow_audio(callback: types.CallbackQuery, data: dict):
    url = data.get("url")
    title = data.get("title", "Slideshow Audio")
    audio_url = data.get("audio")
    chat_id = callback.message.chat.id
    
    if not audio_url:
        await callback.message.edit_text("❌ No audio found in this slideshow.")
        _schedule_next_queued_item(chat_id)
        return
        
    _enter_chat_busy(chat_id)
    progress_msg = await callback.message.edit_text(
        format_downloading_card(
            quality_name="High Quality MP3",
            percent=15,
            downloaded_str="Connecting...",
            total_str="Audio Stream",
            speed_str="High Speed",
            title_label="Downloading Audio"
        ),
        parse_mode="HTML"
    )
    
    import urllib.request
    from aiogram.types import FSInputFile
    
    audio_path = Path(settings.download_dir) / f"slide_audio_{callback.from_user.id}.mp3"
    try:
        def download_audio_file():
            req = urllib.request.Request(audio_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req) as resp, open(audio_path, 'wb') as f:
                f.write(resp.read())
        
        await run_with_progress_ticker(
            asyncio.to_thread(download_audio_file),
            progress_msg=progress_msg,
            render_fn=lambda pct, note: format_downloading_card(
                quality_name="High Quality MP3",
                percent=pct,
                downloaded_str=note,
                total_str="Audio Stream",
                speed_str="High Speed",
                title_label="Downloading Audio"
            ),
            steps=[(20, "Fetching..."), (60, "Extracting..."), (90, "Saving...")],
            interval=1.6,
            edit_first=False
        )
        
        audio_size = os.path.getsize(audio_path) if audio_path.exists() else 0
        caption_text = build_delivery_caption(
            title=title,
            media_type="audio",
            platform="tiktok",
            quality_name="Soundtrack",
            file_size=audio_size
        )

        sent_audio = await run_with_progress_ticker(
            callback.message.reply_audio(
                audio=FSInputFile(audio_path),
                title=title,
                caption=caption_text,
                parse_mode="HTML"
            ),
            progress_msg=progress_msg,
            render_fn=lambda pct, note: format_uploading_card(
                quality_name="Soundtrack MP3",
                percent=pct,
                size_label=format_size(audio_size) if audio_size else "MP3",
                status_note=note,
                title_label="Uploading Audio"
            ),
            steps=[(25, "Sending MP3 to Telegram..."), (65, "Processing audio tags..."), (90, "Finishing up...")],
            interval=1.6
        )
        
        from utils.database import add_download
        await add_download(callback.from_user.id, 'tiktok', url)

        try:
            log_group_id = LOG_GROUP_ID
            user = callback.from_user
            log_text = format_download_log(
                user=user,
                platform_name="TikTok Slideshow Audio 🎵",
                quality_name="Audio MP3",
                file_size=os.path.getsize(audio_path) if audio_path.exists() else 0,
                title=title,
                url=url
            )
            
            if sent_audio and sent_audio.audio:
                file_id = sent_audio.audio.file_id
                await callback.bot.send_audio(
                    chat_id=log_group_id,
                    audio=file_id,
                    caption=log_text,
                    parse_mode="HTML"
                )
        except Exception as log_err:
            logger.error(f"Failed to send slideshow audio log: {log_err}")

        await progress_msg.delete()
        
    except Exception as e:
        logger.error(f"Error handling slideshow audio for {url}: {e}")
        await progress_msg.edit_text(
            f"❌ <b>Download Failed</b>\n\n"
            f"An unexpected error occurred while downloading slideshow audio.\n\n"
            f"💡 <i>Please try again shortly.</i>",
            parse_mode="HTML"
        )
        
        try:
            log_group_id = LOG_GROUP_ID
            user = callback.from_user
            error_log_text = format_error_log(
                user=user,
                platform_name="TikTok Slideshow Audio",
                url=url,
                error_msg=str(e),
                alert_type="Slideshow Audio Alert"
            )
            await callback.bot.send_message(
                chat_id=log_group_id,
                text=error_log_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as admin_log_err:
            logger.error(f"Failed to send slideshow audio error log: {admin_log_err}")
    finally:
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass
        _exit_chat_busy(chat_id)


@router.callback_query(F.data.startswith(QUEUE_REMOVE_PREFIX))
async def handle_queue_remove(callback: types.CallbackQuery):
    """Remove a queued link before it starts analyzing or downloading."""
    chat_id = callback.message.chat.id
    qid = callback.data[len(QUEUE_REMOVE_PREFIX):]
    queue = CHAT_QUEUE.get(chat_id) or []
    removed = False
    for idx, item in enumerate(queue):
        if item["id"] == qid:
            queue.pop(idx)
            removed = True
            break
    if not queue:
        CHAT_QUEUE.pop(chat_id, None)

    await callback.answer("Removed from queue" if removed else "Already started or removed", show_alert=False)
    if removed:
        try:
            await callback.message.edit_text(
                "✖️ <b>Removed from Queue</b>\n\n"
                "<i>This link was skipped.</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await _refresh_queue_positions(chat_id)


@router.callback_query(F.data == TRANSFER_CANCEL)
async def handle_transfer_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Cancel an active download or upload bound to this progress message."""
    key = (callback.message.chat.id, callback.message.message_id)
    entry = ACTIVE_TRANSFERS.get(key)
    if not entry:
        await callback.answer("Transfer is no longer active.", show_alert=False)
        return
    if entry.get("cancelled"):
        await callback.answer("Cancelling...", show_alert=False)
        return

    entry["cancelled"] = True
    entry["event"].set()
    task = entry.get("task")
    if task and not task.done():
        task.cancel()

    await callback.answer("Stopping transfer...", show_alert=False)
    try:
        await callback.message.edit_text(
            "🛑 <b>Cancelling...</b>\n\n"
            "<i>Stopping active transfer and cleaning up temporary files...</i>",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(DownloadStates.waiting_for_quality)
async def process_quality_choice(callback: types.CallbackQuery, state: FSMContext):
    """
    Handle quality selection and start downloading.
    """
    choice = callback.data
    data = await state.get_data()
    
    url = data.get("url")
    title = data.get("title", "video")
    platform = data.get("platform")
    key = (callback.message.chat.id, callback.message.message_id)
    if key in ACTIVE_TRANSFERS and not ACTIVE_TRANSFERS[key]["task"].done():
        await callback.answer("Download already in progress...", show_alert=False)
        return
    
    # Route slideshow handlers if selected
    is_slideshow = data.get("is_slideshow", False)
    if is_slideshow:
        if choice == "slide_photos":
            await state.clear()
            await download_slideshow_photos(callback, data)
            return
        elif choice == "slide_audio":
            await state.clear()
            await download_slideshow_audio(callback, data)
            return
        elif choice == "slide_video":
            is_slideshow = False
            await state.update_data(is_slideshow=False)
            choice = QUALITY_HD
        elif choice == "slide_voice":
            is_slideshow = False
            await state.update_data(is_slideshow=False)
            choice = QUALITY_VOICE
            
    if choice == CANCEL_DOWNLOAD:
        await state.clear()
        await callback.message.edit_text(
            f"✖️ <b>Download Cancelled</b>\n\n"
            f"No problem! Send any link whenever you're ready. 👋",
            parse_mode="HTML"
        )
        await callback.answer()
        _schedule_next_queued_item(callback.message.chat.id)
        return
        
    await callback.answer("Starting download... 🚀")
    
    quality_map = data.get("quality_map") or {}
    if choice in quality_map:
        format_spec = quality_map[choice]["format_spec"]
        quality_name = quality_map[choice]["name"]
    elif choice.startswith(QUALITY_HEIGHT_PREFIX):
        try:
            target_h = int(choice[len(QUALITY_HEIGHT_PREFIX):])
        except ValueError:
            await state.clear()
            await callback.message.edit_text("❌ Invalid selection.")
            return
        format_spec = _build_capped_format_spec(target_h)
        quality_name = _quality_label_for_tier(target_h)[0]
    elif choice == QUALITY_HD:
        format_spec = f"{_build_capped_format_spec(1080)}/bestvideo+bestaudio/best"
        quality_name = "1080p Full HD"
    elif choice == QUALITY_SD:
        format_spec = _build_capped_format_spec(720)
        quality_name = "720p HD"
    elif choice == QUALITY_LOW:
        format_spec = _build_capped_format_spec(360)
        quality_name = "360p SD"
    elif choice == QUALITY_AUDIO:
        format_spec = "bestaudio[ext=m4a]/bestaudio/best"
        quality_name = "Audio (MP3)"
    elif choice == QUALITY_VOICE:
        format_spec = "bestaudio[ext=m4a]/bestaudio/best"
        quality_name = "Voice Message"
    else:
        await state.clear()
        await callback.message.edit_text("❌ Invalid selection.")
        return

    cancel_markup = build_transfer_cancel_markup()
    progress_msg = await callback.message.edit_text(
        f"📥 <b>Downloading...</b>\n\n"
        f"⚙️ <b>Quality:</b> {quality_name}\n"
        f"⏳ <i>Connecting to media stream...</i>",
        parse_mode="HTML",
        reply_markup=cancel_markup
    )
    
    cancel_event = threading.Event()
    transfer_task = asyncio.create_task(
        execute_download_flow(
            bot=callback.bot,
            user=callback.from_user,
            chat_id=callback.message.chat.id,
            progress_msg=progress_msg,
            data=data,
            choice=choice,
            quality_name=quality_name,
            format_spec=format_spec,
            url=url,
            platform=platform,
            title=title,
            state=state,
            cancel_event=cancel_event
        )
    )
    ACTIVE_TRANSFERS[key] = {
        "event": cancel_event,
        "task": transfer_task,
        "cancelled": False,
    }

async def split_and_upload_video(
    bot,
    chat_id,
    filepath: str,
    title: str,
    platform: str,
    quality_name: str,
    total_duration: int,
    width_val: int,
    height_val: int,
    thumbnail_path: Path,
    progress_msg,
    user,
    url: str,
    cancel_event=None
):
    """
    Fast, zero-CPU stream copy splitter for videos exceeding 1.9GB.
    Splits video into parts < 1.9GB and uploads each part via Userbot.
    """
    import math, subprocess
    file_size = os.path.getsize(filepath)
    chunk_size = int(1.8 * 1024 * 1024 * 1024)
    num_parts = math.ceil(file_size / chunk_size)
    if num_parts < 2:
        num_parts = 2
        
    part_duration = total_duration / num_parts if total_duration > 0 else 600
    cancel_markup = build_transfer_cancel_markup()

    if not (cancel_event and cancel_event.is_set()):
        await progress_msg.edit_text(
            f"✂️ <b>Splitting Large Video</b>\n\n"
            f"📦 <b>Total Size:</b> {format_size(file_size)}\n"
            f"✂️ <b>Parts:</b> {num_parts} parts (< 1.9GB each)\n"
            f"⚡ <i>Processing fast stream copy...</i>",
            parse_mode="HTML",
            reply_markup=cancel_markup
        )
    
    from utils.userbot import upload_large_file
    from utils.database import add_download
    platform_emoji = {'tiktok': '🎵', 'instagram': '📸', 'pinterest': '📌', 'facebook': '📘', 'youtube': '❤️', 'twitter': '🐦', 'soundcloud': '☁️'}
    p_emoji = platform_emoji.get(platform, '🌐')
    
    for part_idx in range(1, num_parts + 1):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled by user")
        start_sec = (part_idx - 1) * part_duration
        part_path = f"{filepath}_part{part_idx}.mp4"
        
        def run_split():
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start_sec),
                '-i', str(filepath),
                '-t', str(part_duration),
                '-c', 'copy',
                '-map', '0',
                str(part_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            
        await asyncio.to_thread(run_split)
        
        if cancel_event and cancel_event.is_set():
            if os.path.exists(part_path):
                try: os.remove(part_path)
                except: pass
            raise asyncio.CancelledError("Cancelled by user")

        if not os.path.exists(part_path):
            logger.error(f"Failed to generate split part {part_idx}")
            continue
            
        part_size = os.path.getsize(part_path)
        part_caption = build_delivery_caption(
            title=title,
            media_type="video",
            platform=platform,
            quality_name=quality_name,
            file_size=part_size,
            duration_val=int(part_duration),
            extra_info=f"Part {part_idx}/{num_parts}"
        )

        userbot_last_edit = 0
        async def userbot_progress_callback(percent, downloaded, total, speed_val):
            nonlocal userbot_last_edit
            if cancel_event and cancel_event.is_set():
                return
            now = time.time()
            if now - userbot_last_edit >= 2.0 or percent >= 100.0:
                userbot_last_edit = now
                dl_str = format_size(downloaded)
                tot_str = format_size(total) if total > 0 else format_size(part_size)
                try:
                    await progress_msg.edit_text(
                        format_uploading_card(
                            quality_name=quality_name,
                            percent=percent,
                            size_label=f"{dl_str} / {tot_str}",
                            status_note="Uploading via high-speed server...",
                            title_label=f"Uploading Part {part_idx}/{num_parts}"
                        ),
                        parse_mode="HTML",
                        reply_markup=cancel_markup
                    )
                except Exception:
                    pass
                    
        try:
            msg_id = await upload_large_file(
                part_path,
                progress_callback=userbot_progress_callback,
                duration=int(part_duration),
                width=width_val,
                height=height_val,
                thumb_path=str(thumbnail_path) if (thumbnail_path and thumbnail_path.exists()) else None
            )
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=settings.userbot_channel_id,
                message_id=msg_id,
                caption=part_caption,
                parse_mode="HTML"
            )
        finally:
            if os.path.exists(part_path):
                try: os.remove(part_path)
                except: pass
                
    await add_download(user.id, platform, url)
    try:
        log_group_id = LOG_GROUP_ID
        log_text = format_download_log(
            user=user,
            platform_name=f"{platform.capitalize()} {p_emoji}",
            quality_name=f"{quality_name} ({num_parts} parts)",
            file_size=file_size,
            title=title,
            url=url
        )
        await bot.send_message(chat_id=log_group_id, text=log_text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as log_err:
        logger.error(f"Failed to send split log to admin group: {log_err}")

async def execute_download_flow(bot, user, chat_id, progress_msg, data, choice, quality_name, format_spec, url, platform, title, state=None, cancel_event=None):
    key = (chat_id, progress_msg.message_id)
    if cancel_event is None:
        cancel_event = threading.Event()
    if key not in ACTIVE_TRANSFERS:
        ACTIVE_TRANSFERS[key] = {
            "event": cancel_event,
            "task": asyncio.current_task(),
            "cancelled": False,
        }
    cancel_markup = build_transfer_cancel_markup()
    prefix = f"{platform}_{user.id}_{int(time.time())}"
    awaiting_retry = False

    try:
        if not cancel_event.is_set():
            await progress_msg.edit_text(
                format_downloading_card(
                    quality_name=quality_name,
                    percent=0,
                    downloaded_str="0 B",
                    total_str="Checking...",
                    speed_str="Connecting..."
                ),
                parse_mode="HTML",
                reply_markup=cancel_markup
            )
    except Exception:
        pass

    last_edit_time = 0
    
    async def progress_callback(percent, downloaded, total, speed):
        nonlocal last_edit_time
        if cancel_event.is_set():
            return
        now = time.time()

        # Throttled in-chat status update (every 2.0s or completion) to avoid FloodWait and keep it smooth
        if now - last_edit_time >= 2.0 or percent >= 100.0:
            last_edit_time = now
            speed_str = (format_size(speed) + "/s") if speed else "High Speed"
            downloaded_str = format_size(downloaded)
            total_str = format_size(total) if total > 0 else "Unknown"
            try:
                await progress_msg.edit_text(
                    format_downloading_card(
                        quality_name=quality_name,
                        percent=percent,
                        downloaded_str=downloaded_str,
                        total_str=total_str,
                        speed_str=speed_str
                    ),
                    parse_mode="HTML",
                    reply_markup=cancel_markup
                )
            except Exception:
                pass

    try:
        if cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled by user")

        # Determine direct URL for Invidious fallback or direct streams
        direct_url = None
        if data.get("is_invidious_fallback"):
            invidious_tier = (data.get("quality_map") or {}).get(choice, {}).get("invidious_tier")
            if choice == QUALITY_HD or invidious_tier == "hd":
                direct_url = data.get("hd_url") or data.get("sd_url")
            elif choice == QUALITY_SD or invidious_tier == "sd":
                direct_url = data.get("sd_url") or data.get("hd_url")
            elif choice in [QUALITY_AUDIO, QUALITY_VOICE]:
                direct_url = data.get("audio_url") or data.get("sd_url") or data.get("hd_url")
            else:
                direct_url = data.get("sd_url") or data.get("hd_url")

        filepath = await downloader.download(
            url=url,
            format_spec=format_spec,
            filename_prefix=prefix,
            progress_callback=progress_callback,
            tikwm_video_url=data.get("tikwm_video_url"),
            is_slideshow=bool(data.get("images")),
            images=data.get("images"),
            audio=data.get("audio"),
            direct_url=direct_url,
            cancel_event=cancel_event
        )
        
        if cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled by user")

        if not filepath or not os.path.exists(filepath):
            raise FileNotFoundError("Downloaded file path not resolved.")

        is_voice = choice == QUALITY_VOICE
        if is_voice:
            try:
                await progress_msg.edit_text(
                    f"🎙️ <b>Converting to Voice Note...</b>\n\n"
                    f"⚙️ <i>Processing audio tracks with FFmpeg...</i>",
                    parse_mode="HTML",
                    reply_markup=cancel_markup
                )
            except:
                pass
            voice_path = filepath + "_voice.ogg"
            try:
                await convert_to_voice_message(filepath, voice_path)
                if cancel_event.is_set():
                    if os.path.exists(voice_path):
                        try:
                            os.remove(voice_path)
                        except:
                            pass
                    raise asyncio.CancelledError("Cancelled by user")
                if os.path.exists(voice_path):
                    try:
                        os.remove(filepath)
                    except:
                        pass
                    filepath = voice_path
                else:
                    raise Exception("Voice conversion output file not found.")
            except asyncio.CancelledError:
                raise
            except Exception as conv_err:
                logger.error(f"Voice conversion failed: {conv_err}")
                is_voice = False

        file_size = os.path.getsize(filepath)
        is_video_choice = choice in [QUALITY_HD, QUALITY_SD, QUALITY_LOW] or str(choice).startswith(QUALITY_HEIGHT_PREFIX)
        is_audio = (choice == QUALITY_AUDIO or (not is_video_choice and filepath.endswith(('.mp3', '.m4a', '.aac')))) and not is_voice
        is_image = filepath.endswith(('.jpg', '.jpeg', '.png', '.webp'))
        is_large_file = file_size > 45 * 1024 * 1024
        is_tiktok_slideshow_video = platform == "tiktok" and data.get("images")
        
        if (platform == "soundcloud" or platform == "youtube" or platform == "music") and is_audio:
            source_desc = f"Soft4P Music 🎵 • {quality_name}"
            p_emoji = '🎵'
        else:
            source_desc = "TikTok Slideshow Video 📷📹" if is_tiktok_slideshow_video else f"{platform.capitalize()} • {quality_name}"
            platform_emoji = {'tiktok': '🎵', 'instagram': '📸', 'pinterest': '📌', 'facebook': '📘', 'youtube': '❤️', 'twitter': '🐦', 'soundcloud': '☁️'}
            p_emoji = platform_emoji.get(platform, '🌐')
            
        media_type = "voice" if is_voice else ("audio" if is_audio else ("photo" if is_image else "video"))
        sent_media = None
        duration_val, width_val, height_val, thumbnail_path = None, None, None, None
        
        if not is_image:
            try:
                raw_dur = data.get("duration")
                if raw_dur:
                    duration_val = int(float(raw_dur))
            except:
                pass

            # Always probe video dimensions (width, height) and duration from the video file!
            if not is_audio:
                try:
                    import subprocess, json
                    def probe_video(p):
                        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=width,height,codec_type', '-of', 'json', str(p)]
                        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
                        return json.loads(res.stdout)
                    probe_data = await asyncio.to_thread(probe_video, filepath)
                    if not duration_val and 'format' in probe_data and 'duration' in probe_data['format']:
                        duration_val = int(float(probe_data['format']['duration']))
                    if 'streams' in probe_data:
                        for s in probe_data['streams']:
                            if s.get('codec_type') == 'video':
                                width_val = int(s.get('width', 1280))
                                height_val = int(s.get('height', 720))
                                break
                except Exception as probe_err:
                    logger.error(f"Failed to probe video file: {probe_err}")

            thumbnail_url = data.get("thumbnail")
            raw_thumb_path = Path(settings.download_dir) / f"raw_thumb_{user.id}_{int(time.time())}"
            if thumbnail_url:
                thumbnail_path = Path(settings.download_dir) / f"thumb_{user.id}_{int(time.time())}.jpg"
                try:
                    import urllib.request
                    def download_and_format_thumb():
                        req = urllib.request.Request(thumbnail_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                        with urllib.request.urlopen(req, timeout=10) as resp, open(raw_thumb_path, 'wb') as f:
                            f.write(resp.read())
                        
                        import subprocess
                        if is_audio:
                            # 1:1 square crop for audio album art
                            cmd = ['ffmpeg', '-y', '-i', str(raw_thumb_path), '-vf', "crop=w='min(iw,ih)':h='min(iw,ih)'", '-q:v', '2', str(thumbnail_path)]
                        else:
                            # Standard max 320x320 JPEG scaling for Telegram video previews
                            cmd = ['ffmpeg', '-y', '-i', str(raw_thumb_path), '-vf', "scale=320:320:force_original_aspect_ratio=decrease", '-q:v', '2', str(thumbnail_path)]
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        if raw_thumb_path.exists():
                            try: os.remove(raw_thumb_path)
                            except: pass
                            
                    await asyncio.to_thread(download_and_format_thumb)
                except Exception as thumb_err:
                    logger.error(f"Failed to download/format thumbnail: {thumb_err}")
                    thumbnail_path = None
                    if raw_thumb_path.exists():
                        try: os.remove(raw_thumb_path)
                        except: pass

            # If no thumbnail URL or download failed, extract frame 1 from the video as thumbnail!
            if not is_audio and (not thumbnail_path or not thumbnail_path.exists()):
                thumbnail_path = Path(settings.download_dir) / f"thumb_{user.id}_{int(time.time())}.jpg"
                try:
                    def extract_thumb():
                        cmd = ['ffmpeg', '-y', '-ss', '00:00:01.000', '-i', str(filepath), '-vframes', '1', '-vf', "scale=320:320:force_original_aspect_ratio=decrease", '-q:v', '2', str(thumbnail_path)]
                        import subprocess
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    await asyncio.to_thread(extract_thumb)
                except Exception as extract_err:
                    logger.error(f"Failed to extract frame thumbnail: {extract_err}")
                    thumbnail_path = None

        # Embed cover artwork, performer, and title metadata tags for audio files
        if is_audio:
            file_ext = os.path.splitext(filepath)[1].lower()
            is_copyable = file_ext in [".m4a", ".mp3", ".aac"]
            
            if is_copyable:
                tagged_ext = file_ext
            else:
                tagged_ext = ".mp3" # Fallback if webm/opus is downloaded
                
            tagged_path = filepath + f"_tagged{tagged_ext}"
            performer_name = data.get("uploader") or data.get("artist") or "Soft4P Music"
            song_title = title or "Soft4P Track"
            
            cmd = [
                'ffmpeg', '-y',
                '-i', str(filepath)
            ]
            
            has_cover = thumbnail_path and thumbnail_path.exists()
            if has_cover:
                cmd.extend(['-i', str(thumbnail_path)])
                
            cmd.extend([
                '-map', '0:a'
            ])
            
            if has_cover:
                cmd.extend([
                    '-map', '1:0',
                    '-c:v', 'mjpeg',
                ])
                if tagged_ext == ".mp3":
                    cmd.extend([
                        '-id3v2_version', '3',
                        '-metadata:s:v', 'title=Album cover',
                        '-metadata:s:v', 'comment=Cover (front)'
                    ])
                else:
                    cmd.extend([
                        '-disposition:v', 'attached_pic'
                    ])
                    
            if is_copyable:
                cmd.extend([
                    '-c:a', 'copy', # Copy stream instantly! No slow conversion!
                ])
            else:
                cmd.extend([
                    '-c:a', 'libmp3lame',
                    '-b:a', '192k',
                ])
                
            cmd.extend([
                '-metadata', f'title={song_title}',
                '-metadata', f'artist={performer_name}',
                str(tagged_path)
            ])
            
            try:
                def run_ffmpeg():
                    import subprocess
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                
                await asyncio.to_thread(run_ffmpeg)
                
                if os.path.exists(tagged_path):
                    try:
                        os.remove(filepath)
                    except:
                        pass
                    filepath = tagged_path
                    file_size = os.path.getsize(filepath)
            except Exception as embed_err:
                logger.error(f"Failed to embed metadata and cover art: {embed_err}")

        # Build single-message caption: title + badge info line
        caption_text = build_delivery_caption(
            title=title,
            media_type=media_type,
            platform=platform,
            quality_name=quality_name,
            file_size=file_size,
            duration_val=duration_val
        )

        # Check if video file exceeds 1.9GB Telegram userbot single-file threshold
        if not is_audio and not is_image and file_size > 1900 * 1024 * 1024:
            await split_and_upload_video(
                bot=bot,
                chat_id=chat_id,
                filepath=filepath,
                title=title,
                platform=platform,
                quality_name=quality_name,
                total_duration=duration_val or 0,
                width_val=width_val or 1280,
                height_val=height_val or 720,
                thumbnail_path=thumbnail_path,
                progress_msg=progress_msg,
                user=user,
                url=url,
                cancel_event=cancel_event
            )
            if state: await state.clear()
            try: await progress_msg.delete()
            except: pass
            return

        if is_large_file:
            if not cancel_event.is_set():
                await progress_msg.edit_text(
                    format_uploading_card(
                        quality_name=quality_name,
                        percent=0,
                        size_label=f"0 B / {format_size(file_size)}",
                        status_note="Preparing high-speed upload...",
                        title_label="Uploading Large File"
                    ),
                    parse_mode="HTML",
                    reply_markup=cancel_markup
                )
            userbot_last_edit = 0
            async def userbot_progress_callback(percent, downloaded, total, speed_val):
                nonlocal userbot_last_edit
                if cancel_event.is_set():
                    return
                now = time.time()
                if now - userbot_last_edit >= 2.0 or percent >= 100.0:
                    userbot_last_edit = now
                    dl_str = format_size(downloaded)
                    tot_str = format_size(total) if total > 0 else format_size(file_size)
                    try:
                        await progress_msg.edit_text(
                            format_uploading_card(
                                quality_name=quality_name,
                                percent=percent,
                                size_label=f"{dl_str} / {tot_str}",
                                status_note="Uploading via high-speed server...",
                                title_label="Uploading Large File"
                            ),
                            parse_mode="HTML",
                            reply_markup=cancel_markup
                        )
                    except Exception:
                        pass

            from utils.userbot import upload_large_file
            msg_id = await upload_large_file(filepath, progress_callback=userbot_progress_callback, duration=duration_val, width=width_val, height=height_val, thumb_path=str(thumbnail_path) if (thumbnail_path and thumbnail_path.exists()) else None)
            if cancel_event.is_set():
                raise asyncio.CancelledError("Cancelled by user")
            try:
                await progress_msg.edit_text(
                    format_uploading_card(
                        quality_name=quality_name,
                        percent=98,
                        size_label=format_size(file_size),
                        status_note="Delivering media to chat...",
                        title_label="Uploading Large File"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass
            sent_media = await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=settings.userbot_channel_id,
                message_id=msg_id,
                caption=caption_text,
                parse_mode="HTML"
            )
        else:
            from aiogram.types import FSInputFile
            input_file = FSInputFile(filepath)
            if is_voice:
                upload_coro = bot.send_voice(
                    chat_id=chat_id,
                    voice=input_file,
                    duration=duration_val if duration_val else None,
                    caption=caption_text,
                    parse_mode="HTML"
                )
            elif is_audio:
                thumbnail_file = FSInputFile(str(thumbnail_path)) if thumbnail_path and thumbnail_path.exists() else None
                performer_name = data.get("uploader") or data.get("artist") or "Soft4P Music"
                upload_coro = bot.send_audio(
                    chat_id=chat_id,
                    audio=input_file,
                    title=title,
                    performer=performer_name,
                    duration=duration_val if duration_val else None,
                    caption=caption_text,
                    parse_mode="HTML",
                    thumbnail=thumbnail_file
                )
            elif is_image:
                upload_coro = bot.send_photo(
                    chat_id=chat_id,
                    photo=input_file,
                    caption=caption_text,
                    parse_mode="HTML"
                )
            else:
                thumbnail_file = FSInputFile(str(thumbnail_path)) if thumbnail_path and thumbnail_path.exists() else None
                upload_coro = bot.send_video(
                    chat_id=chat_id,
                    video=input_file,
                    caption=caption_text,
                    parse_mode="HTML",
                    supports_streaming=True,
                    duration=duration_val,
                    width=width_val,
                    height=height_val,
                    thumbnail=thumbnail_file
                )

            sent_media = await run_with_progress_ticker(
                upload_coro,
                progress_msg=progress_msg,
                render_fn=lambda pct, note: format_uploading_card(
                    quality_name=quality_name,
                    percent=pct,
                    size_label=format_size(file_size),
                    status_note=note,
                    title_label="Uploading Media"
                ),
                steps=[
                    (18, "Initializing upload stream..."),
                    (46, "Uploading media to Telegram..."),
                    (74, "Processing delivery packet..."),
                    (92, "Finalizing transfer...")
                ],
                interval=1.5,
                reply_markup=cancel_markup
            )
        from utils.database import add_download
        await add_download(user.id, platform, url)
        try:
            log_group_id = LOG_GROUP_ID
            log_text = format_download_log(
                user=user,
                platform_name=f"{platform.capitalize()} {p_emoji}",
                quality_name=quality_name,
                file_size=file_size,
                title=title,
                url=url
            )
            file_id = None
            if sent_media and hasattr(sent_media, 'video') and sent_media.video: file_id = sent_media.video.file_id
            elif sent_media and hasattr(sent_media, 'audio') and sent_media.audio: file_id = sent_media.audio.file_id
            elif sent_media and hasattr(sent_media, 'voice') and sent_media.voice: file_id = sent_media.voice.file_id
            elif sent_media and hasattr(sent_media, 'photo') and sent_media.photo: file_id = sent_media.photo[-1].file_id
            elif sent_media and hasattr(sent_media, 'document') and sent_media.document: file_id = sent_media.document.file_id
            if file_id:
                if is_voice: await bot.send_voice(chat_id=log_group_id, voice=file_id, caption=log_text, parse_mode="HTML")
                elif is_audio: await bot.send_audio(chat_id=log_group_id, audio=file_id, caption=log_text, parse_mode="HTML")
                elif is_image: await bot.send_photo(chat_id=log_group_id, photo=file_id, caption=log_text, parse_mode="HTML")
                else: await bot.send_video(chat_id=log_group_id, video=file_id, caption=log_text, parse_mode="HTML")
            else: await bot.send_message(chat_id=log_group_id, text=log_text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as log_err: logger.error(f"Failed to send log to admin group: {log_err}")
        if state: await state.clear()
        try: await progress_msg.delete()
        except: pass
    except (asyncio.CancelledError, yt_dlp.utils.DownloadCancelled):
        cancel_event.set()
        if state:
            try:
                await state.clear()
            except Exception:
                pass
        try:
            await progress_msg.edit_text(
                "✖️ <b>Transfer Cancelled</b>\n\n"
                "Stopped and cleaned up temporary files. Send any link whenever you're ready. 👋",
                parse_mode="HTML"
            )
        except Exception:
            pass
    except Exception as e:
        error_msg = str(e)
        if "Entity Too Large" in error_msg or "Request Entity Too Large" in error_msg:
            awaiting_retry = True
            builder = InlineKeyboardBuilder()
            lower_opts = [
                opt for opt in (data.get("quality_map") or {}).values()
                if opt.get("disp_h", 9999) < ((data.get("quality_map") or {}).get(choice, {}).get("disp_h", 720))
            ]
            for cb_key, opt_val in (data.get("quality_map") or {}).items():
                if opt_val in lower_opts:
                    builder.add(types.InlineKeyboardButton(
                        text=f"📉  Try {opt_val['name']}",
                        callback_data=cb_key,
                        style="success"
                    ))
            if not lower_opts:
                if choice != QUALITY_SD: builder.add(types.InlineKeyboardButton(text="📀  Try 720p HD", callback_data=QUALITY_SD, style="success"))
                if choice != QUALITY_LOW: builder.add(types.InlineKeyboardButton(text="📱  Try 360p SD", callback_data=QUALITY_LOW, style="success"))
            builder.add(types.InlineKeyboardButton(text="🎵  Audio Only", callback_data=QUALITY_AUDIO, style="success"))
            builder.add(types.InlineKeyboardButton(text="⬅️  Cancel", callback_data=CANCEL_DOWNLOAD, style="danger"))
            builder.adjust(1)
            await progress_msg.edit_text(
                f"⚠️ <b>File Exceeds Telegram Limit</b>\n\n"
                f"📦 The downloaded video exceeds the <b>49MB</b> limit for direct bot delivery.\n\n"
                f"💡 <i>Try downloading in a lower quality or extract Audio only.</i>",
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        else:
            if state: await state.clear()
            builder = InlineKeyboardBuilder()
            builder.add(types.InlineKeyboardButton(text="🔄  Try Again", callback_data="nav_home", style="success"))
            builder.adjust(1)
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n\n"
                f"Something went wrong during the download. Our team has been notified.\n\n"
                f"💡 <i>Please try again with a public link.</i>",
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        
        # Send details to admin log group
        try:
            log_group_id = LOG_GROUP_ID
            q_name = quality_name or 'Unknown'
            is_cookie_error = any(kw in error_msg.lower() for kw in [
                "confirm you're not a bot", 
                "cookies are no longer valid", 
                "cookies for the authentication", 
                "n challenge solving failed",
                "requested format is not available",
                "format is not available"
            ])
            
            error_log_text = format_error_log(
                user=user,
                platform_name=f"{platform.capitalize()} ({q_name})",
                url=url,
                error_msg=error_msg,
                alert_type="Download Error Alert"
            )
            
            if is_cookie_error:
                is_youtube = "youtube.com" in url or "youtu.be" in url
                platform_name = "YouTube" if is_youtube else "Facebook"
                error_log_text += f"\n\n💡 <b>{platform_name} Cookies Expired/Required!</b>\nSend fresh <code>cookies.txt</code> to update and restart automatically."
                
                # Send direct warning to owner
                try:
                    owner_id = settings.owner_id
                    await bot.send_message(
                        chat_id=owner_id,
                        text=f"🚨 <b>ALERT: {platform_name} Blocked/Cookies Expired!</b>\n\n"
                             f"The bot detected an authentication block. Please export fresh <code>cookies.txt</code> and upload to bot.\n\n"
                             f"🔗 Link: {url}\n"
                             f"❌ Error: <code>{html.escape(error_msg[:200])}</code>",
                        parse_mode="HTML"
                    )
                except Exception as owner_err:
                    logger.error(f"Failed to send direct alert to owner: {owner_err}")

            await bot.send_message(
                chat_id=log_group_id,
                text=error_log_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as admin_log_err:
            logger.error(f"Failed to send error log to group: {admin_log_err}")
    finally:
        current_entry = ACTIVE_TRANSFERS.get(key)
        if current_entry and current_entry.get("event") is cancel_event:
            ACTIVE_TRANSFERS.pop(key, None)
        if cancel_event.is_set():
            downloader._cleanup_prefix_files(prefix)
        # Ensure cleanup of download files
        if 'filepath' in locals() and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"Cleaned up temporary file: {filepath}")
            except Exception as cleanup_err:
                logger.error(f"Failed to delete {filepath}: {cleanup_err}")
                
        # Ensure cleanup of temporary thumbnail
        if 'thumbnail_path' in locals() and thumbnail_path and os.path.exists(thumbnail_path):
            try:
                os.remove(thumbnail_path)
                logger.info(f"Cleaned up temporary thumbnail: {thumbnail_path}")
            except Exception as cleanup_err:
                logger.error(f"Failed to delete thumbnail {thumbnail_path}: {cleanup_err}")
        if not awaiting_retry:
            _schedule_next_queued_item(chat_id)

async def convert_to_voice_message(input_path: str, output_path: str):
    """
    Convert any video or audio file to an OGG file with Opus codec compatible with Telegram voice messages.
    """
    import subprocess
    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-vn',                 # Disable video stream decoding (makes it 100x faster!)
        '-c:a', 'opus',        # Native Opus codec
        '-strict', '-2',       # Allow experimental codec if needed
        '-b:a', '32k',
        '-ac', '1',
        '-ar', '48000',
        str(output_path)
    ]
    def run_ffmpeg():
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            # Capture the last few lines of stderr which contain the actual error details
            err_details = res.stderr[-200:].strip() if res.stderr else "Unknown error"
            logger.error(f"FFmpeg error: {res.stderr}")
            raise RuntimeError(f"FFmpeg failed: {err_details}")
    await asyncio.to_thread(run_ffmpeg)

@router.message(F.video | F.audio | F.voice | F.document)
async def handle_media_message(message: types.Message, state: FSMContext):
    """
    Handle user sending or forwarding a video, audio, or voice file.
    Show them conversion options.
    """
    document = message.document
    video = message.video
    audio = message.audio
    voice = message.voice
    
    file_id = None
    file_name = "Media File"
    file_type = None
    
    if video:
        file_id = video.file_id
        file_name = video.file_name or "video.mp4"
        file_type = "Video 📹"
    elif audio:
        file_id = audio.file_id
        file_name = audio.file_name or f"{audio.title or 'audio'}.mp3"
        file_type = "Audio 🎵"
    elif voice:
        file_id = voice.file_id
        file_name = "voice.ogg"
        file_type = "Voice Message 🎙️"
    elif document:
        mime = document.mime_type or ""
        if mime.startswith("video/") or mime.startswith("audio/"):
            file_id = document.file_id
            file_name = document.file_name or "file"
            file_type = "Document 📄"
            
    if not file_id:
        return

    from utils.database import add_user
    await add_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name
    )

    # Save file ID to state to bypass 64-byte callback_data limit
    await state.update_data(conv_file_id=file_id)

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="🎙️  Convert to Voice Note",
        callback_data="conv_voice",
        style="success"
    ))
    builder.add(types.InlineKeyboardButton(
        text="✖️  Cancel",
        callback_data="conv_cancel",
        style="danger"
    ))
    builder.adjust(1)
    
    await message.reply(
        f"🎙️ <b>Soft4P Voice Converter</b>\n\n"
        f"📦 <b>File Type:</b> {file_type}\n"
        f"📝 <b>Name:</b> <i>{html.escape(file_name[:60])}</i>\n\n"
        f"👇 <i>Choose conversion option below:</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "conv_voice")
async def process_media_conversion(callback: types.CallbackQuery, state: FSMContext):
    state_data = await state.get_data()
    file_id = state_data.get("conv_file_id")
    
    if not file_id:
        await callback.answer("❌ Session expired. Please send/forward the file again.", show_alert=True)
        return
        
    # Clear state right away to prevent double clicks
    await state.clear()
    
    progress_msg = await callback.message.edit_text(
        f"🎙️ <b>Converting to Voice Note</b>\n\n"
        f"📥 <i>Downloading media from Telegram...</i>\n\n"
        f"{format_progress_bar(15)} <b>15%</b>",
        parse_mode="HTML"
    )
    
    input_path = Path(settings.download_dir) / f"conv_input_{callback.from_user.id}_{int(time.time())}"
    output_path = Path(settings.download_dir) / f"conv_output_{callback.from_user.id}_{int(time.time())}.ogg"
    
    try:
        file_info = await callback.bot.get_file(file_id)
        ext = os.path.splitext(file_info.file_path)[1] or ".mp4"
        input_path = input_path.with_name(input_path.name + ext)
        
        await callback.bot.download_file(file_info.file_path, str(input_path))
        
        await progress_msg.edit_text(
            f"🎙️ <b>Converting to Voice Note</b>\n\n"
            f"🎙️ <i>Processing audio tracks with FFmpeg...</i>\n\n"
            f"{format_progress_bar(55)} <b>55%</b>",
            parse_mode="HTML"
        )
        
        await convert_to_voice_message(input_path, output_path)
        
        if not output_path.exists() or os.path.getsize(output_path) == 0:
            raise Exception("FFmpeg conversion failed to produce output.")
            
        conv_size = os.path.getsize(output_path) if output_path.exists() else 0
        caption_text = build_delivery_caption(
            title="Converted Voice Note",
            media_type="voice",
            platform="telegram",
            quality_name="Voice",
            file_size=conv_size
        )

        await run_with_progress_ticker(
            callback.message.reply_voice(
                voice=types.FSInputFile(output_path),
                caption=caption_text,
                parse_mode="HTML"
            ),
            progress_msg=progress_msg,
            render_fn=lambda pct, note: format_uploading_card(
                quality_name="Voice Note",
                percent=pct,
                size_label=format_size(conv_size) if conv_size else "Voice",
                status_note=note,
                title_label="Uploading Voice Note"
            ),
            steps=[(25, "Sending voice note to Telegram..."), (65, "Processing waveform..."), (92, "Finishing up...")],
            interval=1.5
        )
        
        await progress_msg.delete()
        
    except Exception as e:
        logger.error(f"Media conversion to voice failed: {e}")
        await progress_msg.edit_text(
            f"❌ <b>Conversion Failed</b>\n\n"
            f"Could not convert this media file.\n"
            f"Error: <code>{html.escape(str(e)[:150])}</code>",
            parse_mode="HTML"
        )
    finally:
        if input_path.exists():
            try: os.remove(input_path)
            except: pass
        if output_path.exists():
            try: os.remove(output_path)
            except: pass

@router.callback_query(F.data == "conv_cancel")
async def cancel_conversion(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        f"✖️ <b>Conversion Cancelled</b>\n\n"
        f"No problem! Send another media file whenever you're ready. 👋",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "noop")
async def noop_callback(callback: types.CallbackQuery):
    await callback.answer()

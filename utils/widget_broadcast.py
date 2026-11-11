import asyncio
import logging
import zlib
import aiohttp
from aiogram import Bot
from aiogram.types import InputRichMessage

from config import settings
from handlers.start import make_bot_footer
from utils.database import get_all_users

logger = logging.getLogger(__name__)
OWNER_ID = 1334814727
WIDGET_HEIGHT = 258


def _build_pixel_heart_html() -> str:
    """Renders a 9x8 pure HTML/CSS pixelated rose heart using CSS Grid."""
    rows = [
        ".rrr.rrr.",
        "rlhrrrrrd",
        "rhlrrrrdd",
        "rrrrrrrrd",
        ".rrrrrrdd",
        "..rrrrdd.",
        "...rrdd..",
        "....d....",
    ]
    palette = {
        ".": "background:transparent",
        "h": "background:#fff1f2",
        "l": "background:#fecdd3",
        "r": "background:#fb7185",
        "d": "background:#e11d48",
    }
    cells = "".join(
        f'<i style="{palette[ch]}"></i>'
        for row in rows
        for ch in row
    )
    return f'<div class="heart" aria-hidden="true">{cells}</div>'


def get_update_widget_html() -> str:
    """
    Compact Rose & Black Pixel + Liquid Blur HTML Widget
    with a pure HTML/CSS pixelated heart, SOFT4P • UPDATED header, and 3 summary blocks.
    """
    heart_html = _build_pixel_heart_html()
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#08050a;background-image:radial-gradient(circle at 14% 14%,rgba(251,113,133,.5) 0%,rgba(225,29,72,.16) 32%,transparent 62%),radial-gradient(circle at 86% 86%,rgba(244,63,94,.42) 0%,transparent 58%);color:#fff1f2;font-family:ui-monospace,Menlo,Consolas,monospace;padding:8px}}
.glass{{background:rgba(18,9,16,.66);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1.5px solid rgba(253,164,175,.38);border-radius:16px;padding:10px 11px;box-shadow:0 8px 20px rgba(0,0,0,.55)}}
.top{{display:flex;align-items:center;justify-content:space-between;border-bottom:1px dashed rgba(253,164,175,.28);padding-bottom:8px;margin-bottom:8px}}
.brand{{display:flex;align-items:center;gap:9px}}
.heart{{display:grid;grid-template-columns:repeat(9,3px);grid-auto-rows:3px;flex-shrink:0}}
.heart i{{display:block;width:3px;height:3px}}
.name{{font-weight:700;font-size:13px;letter-spacing:1.3px;color:#fff1f2}}
.tag{{background:#2d0f1d;border:1px solid #fb7185;border-radius:999px;color:#fecdd3;font-size:9px;padding:3px 8px;letter-spacing:.9px;font-weight:700}}
.blocks{{display:flex;flex-direction:column;gap:6px;margin-bottom:8px}}
.blk{{display:flex;align-items:flex-start;gap:7px;background:rgba(12,6,11,.62);border:1px solid rgba(253,164,175,.24);border-left:3px solid #fb7185;border-radius:10px;padding:6px 8px}}
.px{{width:5px;height:5px;background:#fb7185;margin-top:4px;flex-shrink:0}}
.bt{{font-size:10.5px;font-weight:700;color:#fecdd3;letter-spacing:.5px;margin-bottom:1px}}
.bd{{font-size:9.5px;color:#d6b2c0;line-height:1.3}}
.foot{{display:flex;align-items:center;justify-content:space-between;font-size:9px;color:#c095a5;padding-top:2px}}
.live{{display:flex;align-items:center;gap:5px;color:#fda4af;font-weight:700}}
.dot{{width:5px;height:5px;background:#fb7185;display:inline-block}}
</style>
</head>
<body>
<div class="glass">
  <div class="top">
    <div class="brand">
      {heart_html}
      <span class="name">SOFT4P</span>
    </div>
    <span class="tag">UPDATED</span>
  </div>
  <div class="blocks">
    <div class="blk">
      <div class="px"></div>
      <div>
        <div class="bt">NEW TELEGRAM UI</div>
        <div class="bd">Redesigned &amp; beautified UI built for latest Telegram updates.</div>
      </div>
    </div>
    <div class="blk">
      <div class="px"></div>
      <div>
        <div class="bt">SPEED BOOSTED</div>
        <div class="bd">Faster bot response, download &amp; upload pipeline.</div>
      </div>
    </div>
    <div class="blk">
      <div class="px"></div>
      <div>
        <div class="bt">YOUTUBE &amp; PLATFORMS FIXED</div>
        <div class="bd">YouTube works smoothly again + FB, IG &amp; TikTok fixes.</div>
      </div>
    </div>
  </div>
  <div class="foot">
    <span class="live"><span class="dot"></span>V2.4 LIVE</span>
    <span>CUTE PIXEL EDITION ♡</span>
  </div>
</div>
</body>
</html>"""


def get_update_rich_blocks() -> list[dict]:
    """
    Rich Message summarizing the Soft4P update inside native Telegram rich bubbles
    ('details' card blocks) along with native in-text buttons.
    """
    return [
        {"type": "heading", "size": 1, "text": "✨ Soft4P Update Log"},
        {
            "type": "paragraph",
            "text": "Here's what we upgraded in Soft4P to give you a cleaner & faster experience:"
        },
        {
            "type": "details",
            "summary": "🎨 Beautified Bot UI (New Telegram Features)",
            "is_open": True,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "Updated and beautified our bot UI using the latest Telegram Bot updates — with native Rich Text cards, sleek in-text buttons, and live progress bars."
                }
            ]
        },
        {
            "type": "details",
            "summary": "⚡ Boosted Bot Speed",
            "is_open": True,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "Boosted link analysis, downloading, and Telegram upload speeds so you get your videos & songs much faster."
                }
            ]
        },
        {
            "type": "details",
            "summary": "🛠 YouTube & Platform Fixes",
            "is_open": True,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "YouTube downloads (which previously didn't work properly) now work smoothly! Also fixed bugs and caption issues across Facebook, Instagram, TikTok & Pinterest."
                }
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "🌐  Supported Platforms", "callback_data": "nav_platforms", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "📖  How to Use", "callback_data": "nav_help", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "📊  My Stats", "callback_data": "nav_mystats", "style": "primary"}]},
        {"type": "buttons", "buttons": [{"text": "🚀  Try Soft4P Now", "url": "https://t.me/soft4pbot", "style": "primary"}]},
        make_bot_footer("Fast, Watermark-Free Downloader"),
    ]


async def send_widget_message(
    session: aiohttp.ClientSession,
    chat_id: int,
    html_code: str,
    width: int = 0,
    height: int = WIDGET_HEIGHT,
) -> dict:
    """Sends an HTML widget via Telegram Bot API 10.4 sendWidget endpoint."""
    api_url = f"https://api.telegram.org/bot{settings.bot_token}/sendWidget"
    payload = {
        "chat_id": chat_id,
        "html": html_code,
        "width": width,
        "height": height,
    }
    async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        return await resp.json()


async def send_owner_preview() -> dict:
    """Sends the compact widget + separate rich text update message ONLY to OWNER_ID."""
    html_code = get_update_widget_html()
    raw_len = len(html_code.encode("utf-8"))
    comp_len = len(zlib.compress(html_code.encode("utf-8"), 9))
    logger.info(f"Widget HTML size: {raw_len}B raw, {comp_len}B compressed")

    rich_msg = InputRichMessage.model_validate({"blocks": get_update_rich_blocks()})
    bot = Bot(token=settings.bot_token)
    try:
        async with aiohttp.ClientSession() as session:
            widget_res = await send_widget_message(session, OWNER_ID, html_code)
        rich_res = await bot.send_rich_message(chat_id=OWNER_ID, rich_message=rich_msg)
        return {
            "widget_ok": widget_res.get("ok"),
            "widget_message_id": (widget_res.get("result") or {}).get("message_id"),
            "widget_error": widget_res.get("description"),
            "rich_message_id": rich_res.message_id,
            "raw_bytes": raw_len,
            "compressed_bytes": comp_len,
        }
    finally:
        await bot.session.close()


async def broadcast_widget_to_all_users() -> tuple[int, int, int]:
    """
    Broadcasts the compact widget + rich text update message to all registered users.
    Returns (success_count, fail_count, total_users).
    """
    users = await get_all_users()
    html_code = get_update_widget_html()
    rich_msg = InputRichMessage.model_validate({"blocks": get_update_rich_blocks()})
    bot = Bot(token=settings.bot_token)
    success_count = 0
    fail_count = 0

    try:
        async with aiohttp.ClientSession() as session:
            for uid in users:
                try:
                    res = await send_widget_message(session, uid, html_code)
                    if not res.get("ok"):
                        retry_after = (res.get("parameters") or {}).get("retry_after")
                        if retry_after:
                            await asyncio.sleep(float(retry_after) + 0.5)
                            res = await send_widget_message(session, uid, html_code)

                    if res.get("ok"):
                        await bot.send_rich_message(chat_id=uid, rich_message=rich_msg)
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    logger.warning(f"Update broadcast failed for user {uid}: {e}")
                    fail_count += 1
                await asyncio.sleep(0.08)
    finally:
        await bot.session.close()

    return success_count, fail_count, len(users)

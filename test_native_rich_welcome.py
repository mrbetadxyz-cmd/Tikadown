import sys
sys.path.insert(0, '/home/ubuntu/softp4')
import asyncio
from aiogram import Bot
from aiogram.types import InputRichMessage
from config import settings

async def main():
    bot = Bot(token=settings.bot_token)
    chat_id = 1334814727
    
    blocks = [
        {"type": "section_heading", "text": "⚡ SOFT4P Premium Downloader"},
        {"type": "paragraph", "text": "Hey there! Welcome aboard.\n\nSend any video link to download watermark-free, or send a song name for instant MP3!"},
        {
            "type": "table",
            "is_bordered": True,
            "is_striped": True,
            "cells": [
                [
                    {"text": "Platform", "is_header": True, "align": "left"},
                    {"text": "Quality", "is_header": True, "align": "center"},
                    {"text": "Status", "is_header": True, "align": "center"}
                ],
                [
                    {"text": "🎥 YouTube", "align": "left"},
                    {"text": "1080p / MP3", "align": "center"},
                    {"text": "✅ Free", "align": "center"}
                ],
                [
                    {"text": "🎵 TikTok", "align": "left"},
                    {"text": "No Watermark", "align": "center"},
                    {"text": "✅ Free", "align": "center"}
                ],
                [
                    {"text": "📸 Instagram", "align": "left"},
                    {"text": "Reels / HD", "align": "center"},
                    {"text": "✅ Free", "align": "center"}
                ],
                [
                    {"text": "📻 Songs", "align": "left"},
                    {"text": "320k Audio", "align": "center"},
                    {"text": "✅ Free", "align": "center"}
                ]
            ]
        },
        {
            "type": "details",
            "summary": {"type": "plain", "text": "📖 Quick How-to Guide (Tap to view)"},
            "is_open": False,
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "1. Copy any link from TikTok, Instagram, YouTube, etc.\n2. Paste & send it into this chat.\n3. Choose your quality from the in-text menu!\n\n💡 Music: Send any song name directly to get the MP3 with cover art!"
                }
            ]
        },
        {"type": "divider"},
        {"type": "buttons", "buttons": [{"text": "📖  How to Use", "callback_data": "nav_help", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "🌐  Supported Sites", "callback_data": "nav_platforms", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "📊  My Stats", "callback_data": "nav_mystats", "style": "success"}]},
        {"type": "buttons", "buttons": [{"text": "💬  Support Channel", "url": "https://t.me/soft4pbot", "style": "primary"}]},
        {"type": "footer", "text": "⚡ Soft4P • Fast, Watermark-Free Downloader"}
    ]
    
    rich_msg = InputRichMessage.model_validate({"blocks": blocks})
    msg = await bot.send_rich_message(chat_id=chat_id, rich_message=rich_msg)
    print("SUCCESS Native Rich Blocks Welcome Message! Message ID:", msg.message_id)
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

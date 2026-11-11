import logging
from typing import Callable, Dict, Any, Awaitable, List
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hbold
from config import settings

logger = logging.getLogger(__name__)

REQUIRED_CHANNELS = [
    {
        "name": "Main Channel",
        "username": "@Chplinz",
        "url": "https://t.me/Chplinz"
    },
    {
        "name": "News & Updates",
        "username": "@soft4pnews",
        "url": "https://t.me/soft4pnews"
    }
]

OWNER_ID = settings.owner_id

async def get_unjoined_channels(bot, user_id: int) -> List[Dict[str, str]]:
    """
    Returns a list of channels that the user has not joined yet.
    """
    if user_id == OWNER_ID:
        return []
    
    unjoined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["username"], user_id=user_id)
            if member.status not in ["creator", "administrator", "member", "restricted"]:
                unjoined.append(ch)
        except Exception as e:
            logger.error(f"Error checking membership for {ch['username']} (user {user_id}): {e}")
            unjoined.append(ch)
    return unjoined

async def is_user_member(bot, user_id: int) -> bool:
    """
    Checks if a user is a member of all required channels.
    """
    unjoined = await get_unjoined_channels(bot, user_id)
    return len(unjoined) == 0

def get_join_keyboard(unjoined_channels: List[Dict[str, str]] = None) -> Any:
    """
    Returns the join channel keyboard markup with styled buttons.
    """
    builder = InlineKeyboardBuilder()
    channels = unjoined_channels if unjoined_channels else REQUIRED_CHANNELS
    for ch in channels:
        builder.add(InlineKeyboardButton(text=f"📢  Join {ch['username']}", url=ch["url"], style="primary"))
    builder.add(InlineKeyboardButton(text="🔄  Verify Membership", callback_data="check_join_status", style="success"))
    builder.adjust(1)
    return builder.as_markup()

def get_join_text(unjoined_channels: List[Dict[str, str]] = None) -> str:
    """
    Returns the force join message text with clean styling.
    """
    channels = unjoined_channels if unjoined_channels else REQUIRED_CHANNELS
    channel_list_text = "\n".join([f"• 📢 <b>{ch.get('name', ch['username'])}</b> ({ch['username']})" for ch in channels])
    return (
        f"⚠️ <b>Channel Membership Required</b>\n\n"
        f"To keep Soft4P free and running, please join our official update channel(s):\n\n"
        f"{channel_list_text}\n\n"
        f"👉 <i>Join the channel(s) below, then click Verify Membership to begin!</i>"
    )

class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Determine the user ID and bot instance
        user_id = None
        bot = data.get("bot")

        if isinstance(event, Message):
            if not event.from_user:
                return await handler(event, data)
            user_id = event.from_user.id
            
            # Check membership
            unjoined = await get_unjoined_channels(bot, user_id)
            if unjoined:
                # Prompt user to join
                try:
                    await event.answer(
                        get_join_text(unjoined),
                        parse_mode="HTML",
                        reply_markup=get_join_keyboard(unjoined)
                    )
                except Exception as e:
                    logger.warning(f"Failed to send force-join message to user {user_id}: {e}")
                return

        elif isinstance(event, CallbackQuery):
            if not event.from_user:
                return await handler(event, data)
            user_id = event.from_user.id

            # Allow the check_join_status callback to bypass this check
            if event.data == "check_join_status":
                return await handler(event, data)

            # Check membership
            unjoined = await get_unjoined_channels(bot, user_id)
            if unjoined:
                unjoined_names = ", ".join([ch["username"] for ch in unjoined])
                try:
                    await event.answer(
                        f"⚠️ Please join {unjoined_names} first!",
                        show_alert=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to answer force-join callback for user {user_id}: {e}")
                try:
                    await event.message.edit_text(
                        get_join_text(unjoined),
                        parse_mode="HTML",
                        reply_markup=get_join_keyboard(unjoined)
                    )
                except Exception:
                    pass
                return

        return await handler(event, data)

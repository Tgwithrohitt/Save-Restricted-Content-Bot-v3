"""Bootstrap commands for the focused Save Post dashboard."""
import logging

from pyrogram import filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

from config import FORCE_SUB
from shared_client import app

logger = logging.getLogger(__name__)


async def subscribe(client, message, user_id=None):
    """Keep optional force-subscription support for save/clone operations."""
    if not FORCE_SUB:
        return 0
    target = user_id if user_id is not None else message.from_user.id
    try:
        member = await client.get_chat_member(FORCE_SUB, target)
        if str(member.status) == "ChatMemberStatus.BANNED":
            await message.reply_text("You are banned from using this bot.")
            return 1
    except UserNotParticipant:
        link = await client.export_chat_invite_link(FORCE_SUB)
        await message.reply_text(
            "Join the required channel to use Save Post and Clone Channel.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL", url=link)]]),
        )
        return 1
    except Exception:
        logger.warning("Could not verify channel subscription")
        await message.reply_text("Could not verify channel subscription. Please try again later.")
        return 1
    return 0


async def set_commands():
    """Expose a single clean Telegram command; all work begins at /start."""
    await app.set_bot_commands([BotCommand("start", "Open Save Post dashboard")])


async def run_start_plugin():
    try:
        await set_commands()
    except Exception:
        # A failed command-list update must not prevent the bot from starting.
        logger.warning("Could not configure bot command list")


@app.on_message(filters.command("set") & filters.private)
async def set_command(_, message):
    """Compatibility command for operators after BotFather/API changes."""
    from config import OWNER_ID

    if message.from_user.id not in OWNER_ID:
        return
    try:
        await set_commands()
        await message.reply_text("✅ Dashboard command list updated.")
    except Exception:
        await message.reply_text("⚠️ Could not update the command list right now.")

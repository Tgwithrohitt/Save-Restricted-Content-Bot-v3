"""Focused private dashboard for saving posts and live channel mirroring.

The public interface deliberately exposes only three dashboard destinations:
Save Post, Settings, and Account.  All secondary screens belong to one of
those destinations so users do not need to navigate payment, downloader, or
batch-management menus.
"""
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard

from shared_client import app
from utils.custom_filters import get_user_step, set_user_step
from utils.func import get_user_data, save_user_bot, save_user_data, users_collection
from plugins import batch, localization, login

logger = logging.getLogger(__name__)

# A user can have one ordinary menu input at a time.  Credentials are never
# stored in this dictionary; it only records the expected input type.
waiting = {}
input_languages = {}
busy_inputs = {}

# Destructive account actions use short-lived, message-bound confirmation
# tokens.  The token is not a credential and is consumed before side effects.
confirmations = {}
CONFIRM_SECONDS = 600


PAGES = {
    "home": (
        "🪞 POST VAULT",
        "<b>Save accessible Telegram posts or keep a channel mirrored.</b>\n\n"
        "Start with <b>Save Post</b>. Set your destination and account once, then use the same clean flow every time.",
        [
            [("📥 𝗦𝗔𝗩𝗘 𝗣𝗢𝗦𝗧", "save")],
            [("⚙️ 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦", "settings"), ("👤 𝗔𝗖𝗖𝗢𝗨𝗡𝗧", "account")],
        ],
        None,
    ),
    "save": (
        "📥 SAVE POST",
        "<b>One clear workspace for saving and cloning.</b>\n\n"
        "• <b>Save a Post</b> copies one Telegram post.\n"
        "• <b>Clone Channel</b> mirrors new channel posts while the bot is online.",
        [
            [("🔗 𝗦𝗔𝗩𝗘 𝗔 𝗣𝗢𝗦𝗧", "save_post")],
            [("🪞 𝗖𝗟𝗢𝗡𝗘 𝗖𝗛𝗔𝗡𝗡𝗘𝗟", "mirror")],
            [("📊 𝗖𝗟𝗢𝗡𝗘 𝗦𝗧𝗔𝗧𝗨𝗦", "mirror_status"), ("⏹️ 𝗦𝗧𝗢𝗣 𝗖𝗟𝗢𝗡𝗘", "mirror_stop")],
        ],
        "home",
    ),
    "settings": (
        "⚙️ SETTINGS",
        "<b>Control where and how your saved posts arrive.</b>\n\n"
        "Choose a destination, set a caption preference, and keep the output exactly how you want it.",
        [
            [("👀 𝗩𝗜𝗘𝗪 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦", "settings_view")],
            [("📍 𝗗𝗘𝗦𝗧𝗜𝗡𝗔𝗧𝗜𝗢𝗡", "destination"), ("💬 𝗖𝗔𝗣𝗧𝗜𝗢𝗡", "caption")],
            [("🔽 𝗠𝗘𝗗𝗜𝗔 𝗙𝗜𝗟𝗧𝗘𝗥", "filters"), ("📝 𝗢𝗥𝗜𝗚𝗜𝗡𝗔𝗟 𝗖𝗔𝗣𝗧𝗜𝗢𝗡", "toggle_caption")],
            [("🌐 𝗟𝗔𝗡𝗚𝗨𝗔𝗚𝗘 / भाषा", "language")],
            [("♻️ 𝗥𝗘𝗦𝗘𝗧 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦", "reset_request")],
        ],
        "home",
    ),
    "filters": (
        "🔽 MEDIA FILTER",
        "Choose which type of post the saver and live clone should deliver.",
        [
            [("📑 𝗔𝗟𝗟", "filter_all"), ("🖼️ 𝗣𝗛𝗢𝗧𝗢", "filter_photo")],
            [("🎬 𝗩𝗜𝗗𝗘𝗢", "filter_video"), ("📄 𝗗𝗢𝗖𝗨𝗠𝗘𝗡𝗧", "filter_document")],
            [("🎵 𝗔𝗨𝗗𝗜𝗢", "filter_audio"), ("📝 𝗧𝗘𝗫𝗧", "filter_text")],
        ],
        "settings",
    ),
    "language": (
        "🌐 LANGUAGE / भाषा",
        "Choose the dashboard language. Some Telegram security prompts may remain in English.",
        [[("🇬🇧 English", "language_en"), ("🇮🇳 हिन्दी", "language_hi")]],
        "settings",
    ),
    "account": (
        "👤 ACCOUNT",
        "<b>Connect only an account and custom bot you trust this deployment with.</b>\n\n"
        "Your connection is used only for saving accessible Telegram content and live channel cloning.",
        [
            [("📊 𝗔𝗖𝗖𝗢𝗨𝗡𝗧 𝗦𝗧𝗔𝗧𝗨𝗦", "status")],
            [("🔐 𝗖𝗢𝗡𝗡𝗘𝗖𝗧 𝗧𝗘𝗟𝗘𝗚𝗥𝗔𝗠", "login")],
            [("🤖 𝗖𝗨𝗦𝗧𝗢𝗠 𝗕𝗢𝗧", "custombot"), ("🚪 𝗟𝗢𝗚 𝗢𝗨𝗧", "logout_request")],
            [("🗑️ 𝗗𝗘𝗟𝗘𝗧𝗘 𝗔𝗖𝗖𝗢𝗨𝗡𝗧 𝗗𝗔𝗧𝗔", "delete_request")],
        ],
        "home",
    ),
    "custombot": (
        "🤖 CUSTOM BOT",
        "<b>Your custom bot sends saved posts to the destination.</b>\n\n"
        "A token grants control of that bot. Send it only in this private chat and revoke exposed tokens with BotFather.",
        [
            [("➕ 𝗔𝗗𝗗 / 𝗥𝗘𝗣𝗟𝗔𝗖𝗘 𝗕𝗢𝗧", "addbot")],
            [("🗑️ 𝗥𝗘𝗠𝗢𝗩𝗘 𝗕𝗢𝗧", "removebot_request")],
        ],
        "account",
    ),
}

PROMPTS = {
    "destination": (
        "📍 DESTINATION",
        "Send the numeric chat ID where posts should arrive. You can add <code>/topic-id</code> for a forum topic.\n\nExample: <code>-1001234567890</code>",
    ),
    "caption": (
        "💬 CAPTION",
        "Send the caption text to append to saved media. It can be changed any time from Settings.",
    ),
    "mirror": (
        "🪞 CLONE CHANNEL",
        "First connect Telegram, add your custom bot, and set a destination. Then send the source <code>@username</code> or numeric chat ID.\n\nOnly <b>new</b> posts are mirrored while this bot is online. Existing or missed posts are not backfilled.",
    ),
    "addbot": (
        "🤖 CUSTOM BOT",
        "Send your custom bot token in this private chat only. The bot will try to delete your message after reading it. Token format is checked locally; Telegram validity is checked when the bot connects.",
    ),
}


def page(title, body):
    """Render a compact, consistent Telegram dashboard card."""
    return (
        f"<b>{title}</b>\n"
        f"<blockquote>{body}</blockquote>\n\n"
        "<i>Save smart. Mirror clean.</i>"
    )


def markup(rows, back=None, language="en"):
    buttons = [
        [Button(localization.button_label(action, label, language), callback_data="ui:" + action) for label, action in row]
        for row in rows
    ]
    if back:
        home_label = "🏠 होम" if language == "hi" else "🏠 𝗛𝗢𝗠𝗘"
        if back == "home":
            buttons.append([Button(home_label, callback_data="ui:home")])
        else:
            back_label = "🔙 वापस" if language == "hi" else "🔙 𝗕𝗔𝗖𝗞"
            buttons.append([Button(back_label, callback_data="ui:" + back), Button(home_label, callback_data="ui:home")])
    return Keyboard(buttons)


async def language_for(uid):
    return await localization.language_for(uid)


async def render(message, name, edit=False, language="en"):
    title, body, rows, back = PAGES[name]
    if language == "hi":
        title, body = HINDI_PAGES.get(name, (title, body))
    sender = message.edit_text if edit else message.reply_text
    await sender(page(title, body), reply_markup=markup(rows, back, language), parse_mode=ParseMode.HTML)


HINDI_PAGES = {
    "home": ("🪞 पोस्ट वॉल्ट", "<b>टेलीग्राम पोस्ट सेव करें या चैनल को लाइव क्लोन करें।</b>\n\nशुरू करने के लिए <b>पोस्ट सेव करें</b> दबाएँ।"),
    "save": ("📥 पोस्ट सेव करें", "<b>सेव और क्लोन के लिए एक साफ़ जगह।</b>\n\n• एक पोस्ट सेव करें।\n• चैनल क्लोन से नए पोस्ट लाइव मिरर करें।"),
    "settings": ("⚙️ सेटिंग्स", "<b>पोस्ट कहाँ और कैसे भेजनी है, यह नियंत्रित करें।</b>\n\nडेस्टिनेशन और कैप्शन सेट करें।"),
    "account": ("👤 अकाउंट", "<b>केवल भरोसेमंद अकाउंट और कस्टम बॉट कनेक्ट करें।</b>\n\nयही कनेक्शन पोस्ट सेव और चैनल क्लोन के लिए उपयोग होगा।"),
    "custombot": ("🤖 कस्टम बॉट", "<b>कस्टम बॉट सेव की गई पोस्ट डेस्टिनेशन पर भेजता है।</b>\n\nटोकन केवल इस निजी चैट में भेजें।"),
    "filters": ("🔽 मीडिया फ़िल्टर", "सेव और लाइव क्लोन में भेजे जाने वाले पोस्ट का प्रकार चुनें।"),
    "language": ("🌐 भाषा / Language", "डैशबोर्ड की भाषा चुनें। कुछ सुरक्षा संदेश अंग्रेज़ी में रह सकते हैं।"),
}


def fingerprint(token, value):
    """Bind a credential-removal confirmation without retaining the credential."""
    encoded = repr((type(value).__name__, value)).encode("utf-8")
    return hmac.new(token.encode("ascii"), encoded, hashlib.sha256).hexdigest()


async def clear_abandoned_input(uid):
    """Clear regular menu prompts but never silently cancel a running delivery."""
    waiting.pop(uid, None)
    input_languages.pop(uid, None)
    flow = batch.Z.get(uid)
    if flow and flow.get("step") in {"start", "start_single", "count", "validating"}:
        batch.Z.pop(uid, None)


async def navigate(message, uid, name, edit=True):
    from plugins.batch_queue import lock_for

    async with lock_for(uid):
        if get_user_step(uid):
            language = login.login_cache.get(uid, {}).get("language", "en")
            text = "पहले लॉगिन पूरा करें या रद्द करें दबाएँ।" if language == "hi" else "Finish login or tap Cancel before navigating."
            await message.reply_text(
                page("⚠️ लॉगिन जारी है" if language == "hi" else "⚠️ LOGIN IN PROGRESS", text),
                reply_markup=markup([[("❌ CANCEL", "cancel")]], "account", language),
                parse_mode=ParseMode.HTML,
            )
            return
        confirmations.pop(uid, None)
        await clear_abandoned_input(uid)
    await render(message, name, edit=edit, language=await language_for(uid))


@app.on_message(filters.private & filters.command("start"))
async def start_menu(_, message):
    uid = message.from_user.id
    if uid in busy_inputs:
        await message.reply_text("⏳ Your previous input is still being processed. Please wait.")
        return
    await navigate(message, uid, "home", edit=False)


async def select_prompt(query, uid, action):
    from plugins.batch_queue import lock_for

    async with lock_for(uid):
        language = await language_for(uid)
        if get_user_step(uid) or uid in waiting or uid in batch.Z or uid in busy_inputs:
            await query.message.reply_text(
                page("⚠️ कार्रवाई जारी है" if language == "hi" else "⚠️ ACTION IN PROGRESS", "पहले मौजूदा इनपुट रद्द या पूरा करें।" if language == "hi" else "Finish or cancel the current input first."),
                reply_markup=markup([[("❌ CANCEL", "cancel")]], "home", language),
                parse_mode=ParseMode.HTML,
            )
            return
        waiting[uid] = action
        if action == "addbot":
            input_languages[uid] = language
        title, prompt = PROMPTS[action]
        if language == "hi":
            title, prompt = HINDI_PROMPTS.get(action, (title, prompt))
        try:
            await query.message.reply_text(
                page(title, prompt),
                reply_markup=markup([[("❌ CANCEL", "cancel")]], "home", language),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            waiting.pop(uid, None)
            input_languages.pop(uid, None)
            raise


HINDI_PROMPTS = {
    "destination": ("📍 डेस्टिनेशन", "वह संख्यात्मक चैट ID भेजें जहाँ पोस्ट जानी है। फ़ोरम टॉपिक के लिए <code>/topic-id</code> जोड़ सकते हैं।"),
    "caption": ("💬 कैप्शन", "मीडिया के साथ जोड़ने के लिए कैप्शन टेक्स्ट भेजें।"),
    "mirror": ("🪞 चैनल क्लोन", "पहले टेलीग्राम, कस्टम बॉट और डेस्टिनेशन जोड़ें। फिर स्रोत का <code>@username</code> या संख्यात्मक चैट ID भेजें। केवल ऑनलाइन रहते समय आने वाले नए पोस्ट क्लोन होंगे।"),
    "addbot": ("🤖 कस्टम बॉट", "इस निजी चैट में अपना कस्टम बॉट टोकन भेजें। पढ़ने के बाद संदेश हटाने की कोशिश की जाएगी।"),
}


async def start_single_post(client, query, uid):
    language = await language_for(uid)
    if get_user_step(uid):
        await query.message.reply_text("⚠️ पहले लॉगिन पूरा करें या रद्द करें दबाएँ।" if language == "hi" else "⚠️ Finish or cancel login first.")
        return
    await batch.begin_processing(client, query.message, uid, "single")


async def begin_login(query, uid):
    from plugins.batch_queue import lock_for

    async with lock_for(uid):
        language = await language_for(uid)
        if batch.is_user_active(uid):
            await query.message.reply_text("⚠️ पहले चल रही पोस्ट सेव होने दें या उसे रोकें।" if language == "hi" else "⚠️ Let the current post finish before connecting Telegram.")
            return
        if get_user_step(uid) or uid in login.login_cache:
            await query.message.reply_text(
                "⚠️ लॉगिन पहले से जारी है। पूरा करें या रद्द करें दबाएँ।" if language == "hi" else "⚠️ A login is already in progress. Finish it or tap Cancel.",
                reply_markup=markup([[("❌ CANCEL", "cancel")]], language=language),
            )
            return
        await clear_abandoned_input(uid)
        try:
            await login.begin_login(query.message, uid, language)
        except RuntimeError:
            await query.message.reply_text("⚠️ Login is already in progress.")


async def cancel_input(query, uid):
    from plugins.batch_queue import lock_for

    async with lock_for(uid):
        language = await language_for(uid)
        await clear_abandoned_input(uid)
        cache = login.login_cache.pop(uid, {})
        if cache.get("temp_client"):
            try:
                await cache["temp_client"].disconnect()
            except Exception:
                logger.warning("Could not disconnect temporary login client")
        set_user_step(uid, None)
    text = "इनपुट रद्द कर दिया गया।" if language == "hi" else "Input cancelled."
    await query.message.reply_text(page("✅ रद्द" if language == "hi" else "✅ CANCELLED", text), reply_markup=markup([], "home", language), parse_mode=ParseMode.HTML)


async def request_confirmation(query, uid, kind):
    from plugins.batch_queue import lock_for

    details = {
        "logout": ("Log out and remove the saved Telegram session? The live clone will be stopped first.", "सेव टेलीग्राम सेशन हटाएँ? लाइव क्लोन पहले बंद होगा।"),
        "removebot": ("Remove the stored custom-bot token? Revoke it with BotFather if needed. The live clone will be stopped first.", "कस्टम बॉट टोकन हटाएँ? ज़रूरत हो तो BotFather से रद्द करें। लाइव क्लोन पहले बंद होगा।"),
        "reset": ("Reset destination, caption, media filter and caption preference? Your Telegram account and custom bot stay connected.", "डेस्टिनेशन, कैप्शन, मीडिया फ़िल्टर और कैप्शन पसंद रीसेट करें? टेलीग्राम अकाउंट और कस्टम बॉट जुड़े रहेंगे।"),
        "delete": ("Delete all saved settings, credentials and clone data? Telegram session revocation is best effort. This cannot be undone.", "सभी सेव सेटिंग्स, credentials और क्लोन डेटा हटाएँ? Telegram session हटाने का प्रयास होगा। यह वापस नहीं होगा।"),
    }
    async with lock_for(uid):
        language = await language_for(uid)
        credential = None
        if kind in {"logout", "removebot"}:
            field = "session_string" if kind == "logout" else "bot_token"
            try:
                record = await users_collection.find_one({"user_id": uid}, {field: 1, "_id": 0}) or {}
                credential = record.get(field)
            except Exception:
                await query.message.reply_text("⚠️ Could not verify stored credentials. Try again later.")
                return
        token = secrets.token_hex(8)
        confirmations[uid] = {
            "kind": kind,
            "token": token,
            "expires": time.monotonic() + CONFIRM_SECONDS,
            "message_id": query.message.id,
            "fingerprint": fingerprint(token, credential) if kind in {"logout", "removebot"} else None,
        }
    text = details[kind][1 if language == "hi" else 0] + (" पुष्टि 10 मिनट में समाप्त होगी।" if language == "hi" else " Confirmation expires in 10 minutes.")
    await query.message.edit_text(
        page("⚠️ पुष्टि करें" if language == "hi" else "⚠️ CONFIRM", text),
        reply_markup=markup([[("✅ YES", f"confirm:{kind}:{token}"), ("❌ NO", "home")]], language=language),
        parse_mode=ParseMode.HTML,
    )


async def consume_confirmation(uid, action, message_id):
    match = re.fullmatch(r"confirm:(logout|removebot|reset|delete):([0-9a-f]{16})", action)
    item = confirmations.get(uid)
    if not match or not item or item["expires"] < time.monotonic() or item["message_id"] != message_id:
        confirmations.pop(uid, None)
        return None
    if item["kind"] != match.group(1) or not secrets.compare_digest(item["token"], match.group(2)):
        return None
    return confirmations.pop(uid)


async def show_clone_status(message, uid):
    from plugins.mirror import status_snapshot

    language = await language_for(uid)
    try:
        local, saved = await status_snapshot(uid)
        local_labels = {
            "running": "Running" if language != "hi" else "चालू",
            "stopping": "Stopping" if language != "hi" else "रुक रहा है",
            "inactive": "Not active in this worker" if language != "hi" else "इस वर्कर में चालू नहीं",
        }
        saved_labels = {
            "running": "Saved as running" if language != "hi" else "चालू के रूप में सेव",
            "stopped": "Saved as stopped" if language != "hi" else "रुका हुआ सेव",
            "error": "Error" if language != "hi" else "त्रुटि",
        }
        saved_state = saved_labels.get((saved or {}).get("status"), "No saved clone" if language != "hi" else "कोई सेव क्लोन नहीं")
        body = (
            f"🖥️ <b>Worker:</b> {local_labels.get(local, 'Unknown')}\n"
            f"💾 <b>Saved state:</b> {saved_state}\n\n"
            "The clone handles new posts only while online; it does not backfill missed posts."
            if language != "hi"
            else f"🖥️ <b>वर्कर:</b> {local_labels.get(local, 'अज्ञात')}\n💾 <b>सेव स्थिति:</b> {saved_state}\n\nक्लोन केवल ऑनलाइन रहते समय नए पोस्ट संभालता है; छूटे पोस्ट वापस नहीं आते।"
        )
    except Exception:
        logger.warning("Could not load clone status")
        body = "क्लोन स्थिति नहीं मिल सकी। फिर कोशिश करें।" if language == "hi" else "Could not load clone status. Please try again."
    await message.edit_text(
        page("📊 क्लोन स्थिति" if language == "hi" else "📊 CLONE STATUS", body),
        reply_markup=markup([[("🪞 CLONE CHANNEL", "mirror"), ("⏹️ STOP CLONE", "mirror_stop")]], "save", language),
        parse_mode=ParseMode.HTML,
    )


async def perform_stop_clone(message, uid):
    from plugins.mirror import stop_mirror
    from plugins.batch_queue import lock_for

    async with lock_for(uid):
        language = await language_for(uid)
        try:
            stopped = await stop_mirror(uid, drain=True)
            body = ("लाइव क्लोन बंद हो गया।" if stopped else "इस वर्कर में कोई लाइव क्लोन नहीं था; सेव स्थिति बंद की गई है।") if language == "hi" else ("Live clone stopped." if stopped else "No live clone was active here; any saved clone was marked stopped.")
        except Exception:
            logger.warning("Could not confirm clone stop")
            body = "क्लोन बंद होने की पुष्टि नहीं हुई। फिर कोशिश करें।" if language == "hi" else "Could not confirm the clone stopped. Please try again."
    await message.edit_text(page("🪞 लाइव क्लोन" if language == "hi" else "🪞 LIVE CLONE", body), reply_markup=markup([], "save", language), parse_mode=ParseMode.HTML)


async def show_account_status(message, uid):
    language = await language_for(uid)
    try:
        data = await users_collection.find_one({"user_id": uid}, {"session_string": 1, "bot_token": 1, "_id": 0}) or {}
        session = "Connected" if data.get("session_string") else "Not connected"
        bot = "Added" if data.get("bot_token") else "Not added"
        if language == "hi":
            body = f"🔐 <b>Telegram:</b> {'कनेक्टेड' if data.get('session_string') else 'कनेक्ट नहीं है'}\n🤖 <b>कस्टम बॉट:</b> {'जोड़ा गया' if data.get('bot_token') else 'नहीं जोड़ा गया'}\n\nसेव होने का मतलब यह नहीं कि सेशन या टोकन अभी भी मान्य है।"
        else:
            body = f"🔐 <b>Telegram:</b> {session}\n🤖 <b>Custom bot:</b> {bot}\n\nSaved does not verify that a session or token is still valid."
    except Exception:
        logger.warning("Could not load account status")
        body = "अकाउंट स्थिति नहीं मिल सकी।" if language == "hi" else "Could not load account status."
    await message.edit_text(page("📊 अकाउंट स्थिति" if language == "hi" else "📊 ACCOUNT STATUS", body), reply_markup=markup([], "account", language), parse_mode=ParseMode.HTML)


async def reset_settings(message, uid):
    fields = ("chat_id", "caption", "media_filter", "remove_caption")
    language = await language_for(uid)
    try:
        await users_collection.update_one({"user_id": uid}, {"$unset": {key: "" for key in fields}})
        body = "डेस्टिनेशन और कंटेंट सेटिंग्स रीसेट हो गईं।" if language == "hi" else "Destination and content settings were reset."
    except Exception:
        logger.warning("Could not reset settings")
        body = "सेटिंग्स रीसेट होने की पुष्टि नहीं हुई।" if language == "hi" else "Could not confirm settings reset."
    await message.edit_text(page("♻️ रीसेट" if language == "hi" else "♻️ SETTINGS RESET", body), reply_markup=markup([], "settings", language), parse_mode=ParseMode.HTML)


async def remove_credential(message, uid, kind, item):
    from plugins.batch_queue import lock_for
    from plugins.mirror import stop_mirror

    language = await language_for(uid)
    field = "session_string" if kind == "logout" else "bot_token"
    async with lock_for(uid):
        try:
            record = await users_collection.find_one({"user_id": uid}, {field: 1, "_id": 0}) or {}
            value = record.get(field)
            if not hmac.compare_digest(item["fingerprint"], fingerprint(item["token"], value)):
                raise ValueError("Stored credential changed")
        except Exception:
            body = "सेव जानकारी बदल गई है या जाँची नहीं जा सकी। फिर से अनुरोध करें।" if language == "hi" else "Stored credential changed or could not be checked. Request removal again."
            await message.edit_text(page("⚠️ अकाउंट" if language == "hi" else "⚠️ ACCOUNT", body), reply_markup=markup([], "account", language), parse_mode=ParseMode.HTML)
            return
        try:
            await stop_mirror(uid, drain=True)
        except Exception:
            body = "पहले लाइव क्लोन बंद होने की पुष्टि नहीं हुई। फिर कोशिश करें।" if language == "hi" else "Could not confirm the live clone stopped. Try again."
            await message.edit_text(page("⚠️ अकाउंट" if language == "hi" else "⚠️ ACCOUNT", body), reply_markup=markup([], "account", language), parse_mode=ParseMode.HTML)
            return
        if kind == "logout":
            revoked = False
            if value:
                active = batch.UC.pop(uid, None)
                if active:
                    try:
                        await active.stop()
                    except Exception:
                        logger.warning("Could not stop active user client during logout")
                temporary = None
                try:
                    from config import API_HASH, API_ID
                    from utils.encrypt import dcs
                    temporary = Client(f"logout_{uid}", api_id=API_ID, api_hash=API_HASH, session_string=dcs(value), in_memory=True)
                    await temporary.connect()
                    await temporary.log_out()
                    revoked = True
                except Exception:
                    logger.warning("Could not revoke Telegram session")
                finally:
                    if temporary:
                        try:
                            await temporary.disconnect()
                        except Exception:
                            pass
            result = await users_collection.update_one({"user_id": uid, field: value}, {"$unset": {field: ""}})
            if value is None:
                body = "कोई सेव टेलीग्राम सेशन नहीं मिला।" if language == "hi" else "No stored Telegram session was found."
            elif not result.modified_count:
                body = "सेशन हटने की पुष्टि नहीं हुई। Telegram Settings → Devices जाँचें।" if language == "hi" else "Could not confirm session removal. Check Telegram Settings → Devices."
            elif language == "hi":
                body = "टेलीग्राम सेशन हटा दिया गया।" if revoked else "सेव सेशन हटा दिया गया। Telegram Settings → Devices में बचा हुआ सेशन जाँचें।"
            else:
                body = "Telegram session revoked and removed." if revoked else "Stored session removed. Check Telegram Settings → Devices for any remaining session."
            target = "account"
            title = "🚪 लॉगआउट" if language == "hi" else "🚪 LOGGED OUT"
        else:
            active = batch.UB.pop(uid, None)
            if active:
                try:
                    await active.stop()
                except Exception:
                    logger.warning("Could not stop custom bot")
            result = await users_collection.update_one({"user_id": uid, field: value}, {"$unset": {field: ""}})
            for path in (f"user_{uid}.session", f"user_{uid}.session-journal"):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            if value is None:
                body = "कोई सेव कस्टम बॉट टोकन नहीं मिला।" if language == "hi" else "No stored custom-bot token was found."
            elif result.modified_count:
                body = "कस्टम बॉट हटा दिया गया। ज़रूरत हो तो BotFather से टोकन रद्द करें।" if language == "hi" else "Custom bot removed. Revoke its token with BotFather if needed."
            else:
                body = "टोकन हटने की पुष्टि नहीं हुई।" if language == "hi" else "Could not confirm token removal."
            target = "custombot"
            title = "🤖 कस्टम बॉट" if language == "hi" else "🤖 CUSTOM BOT"
    await message.edit_text(page(title, body), reply_markup=markup([], target, language), parse_mode=ParseMode.HTML)


async def delete_account_data(message, uid):
    from plugins.batch_queue import lock_for, jobs, history
    from plugins.mirror import mirrors, stop_mirror

    language = await language_for(uid)
    async with lock_for(uid):
        if batch.is_user_active(uid) or get_user_step(uid):
            body = "पहले चल रही पोस्ट या लॉगिन पूरा करें।" if language == "hi" else "Finish the current post or login before deleting data."
            await message.edit_text(page("⚠️ पहले पूरा करें" if language == "hi" else "⚠️ FINISH FIRST", body), reply_markup=markup([], "account", language), parse_mode=ParseMode.HTML)
            return
        try:
            record = await users_collection.find_one({"user_id": uid}, {"session_string": 1, "_id": 0}) or {}
            await stop_mirror(uid, drain=True)
            await mirrors.delete_one({"user_id": uid})
            await jobs.delete_one({"_id": uid})
            await history.delete_many({"user_id": uid})
            user_client = batch.UC.pop(uid, None)
            custom_bot = batch.UB.pop(uid, None)
            for client in (user_client, custom_bot):
                if client:
                    try:
                        await client.stop()
                    except Exception:
                        logger.warning("Could not stop client during account deletion")
            session = record.get("session_string")
            if session:
                try:
                    from config import API_HASH, API_ID
                    from utils.encrypt import dcs
                    temporary = Client(f"privacy_{uid}", api_id=API_ID, api_hash=API_HASH, session_string=dcs(session), in_memory=True)
                    await temporary.connect()
                    await temporary.log_out()
                    await temporary.disconnect()
                except Exception:
                    logger.warning("Could not revoke session during account deletion")
            await users_collection.delete_many({"user_id": uid})
            for path in (f"{uid}.jpg", f"{uid}_client.session", f"{uid}_client.session-journal", f"user_{uid}.session", f"user_{uid}.session-journal"):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            await clear_abandoned_input(uid)
            body = "अकाउंट डेटा, सेव credentials और क्लोन डेटा हटा दिया गया। Telegram Settings → Devices जाँचें।" if language == "hi" else "Account data, saved credentials and clone data were deleted. Check Telegram Settings → Devices."
            title = "🗑️ डेटा हटाया" if language == "hi" else "🗑️ DATA DELETED"
            target = "home"
        except Exception:
            logger.warning("Account deletion incomplete or uncertain")
            body = "डेटा हटाने की पुष्टि नहीं हुई; कुछ डेटा हट चुका हो सकता है। फिर कोशिश करें।" if language == "hi" else "Could not confirm deletion; some data may already be removed. Please try again."
            title = "⚠️ डेटा हटाना" if language == "hi" else "⚠️ DELETE DATA"
            target = "account"
    await message.edit_text(page(title, body), reply_markup=markup([], target, language), parse_mode=ParseMode.HTML)


@app.on_callback_query(filters.regex(r"^ui:"))
async def menu_callback(client, query):
    if not query.message or query.message.chat.type.value != "private":
        await query.answer("Open the bot in a private chat.", show_alert=True)
        return
    uid = query.from_user.id
    if query.message.chat.id != uid:
        await query.answer("Open the bot in your own private chat.", show_alert=True)
        return

    action = query.data[3:]
    # Any navigation invalidates a destructive confirmation, but its own
    # confirmation callback must be allowed to consume the one-use token.
    if not action.startswith("confirm:"):
        confirmations.pop(uid, None)
    await query.answer()

    if uid in busy_inputs:
        await query.message.reply_text("⏳ Your previous input is still being processed. Please wait.")
        return

    if action in PAGES:
        await navigate(query.message, uid, action)
        return
    if action == "save_post":
        await start_single_post(client, query, uid)
        return
    if action in PROMPTS:
        await select_prompt(query, uid, action)
        return
    if action == "login":
        await begin_login(query, uid)
        return
    if action == "cancel":
        await cancel_input(query, uid)
        return
    if action == "mirror_status":
        await show_clone_status(query.message, uid)
        return
    if action == "mirror_stop":
        await perform_stop_clone(query.message, uid)
        return
    if action == "status":
        await show_account_status(query.message, uid)
        return
    if action == "settings_view":
        from plugins.settings_view import show_settings
        try:
            await show_settings(query.message, uid)
        except Exception:
            logger.warning("Could not load settings")
            language = await language_for(uid)
            await query.message.edit_text(page("⚠️ सेटिंग्स" if language == "hi" else "⚠️ SETTINGS", "सेटिंग्स नहीं मिल सकीं।" if language == "hi" else "Could not load settings."), reply_markup=markup([], "settings", language), parse_mode=ParseMode.HTML)
        return
    if action in {"language_en", "language_hi"}:
        language = action.rsplit("_", 1)[1]
        try:
            result = await users_collection.update_one({"user_id": uid}, {"$set": {"language": language}}, upsert=True)
            if not result.acknowledged:
                raise RuntimeError("Language preference write was not acknowledged")
            await render(query.message, "settings", edit=True, language=language)
        except Exception:
            await query.message.reply_text("⚠️ Could not save language preference.")
        return
    if action.startswith("filter_"):
        selected = action[7:]
        if selected not in {"all", "photo", "video", "document", "audio", "text"}:
            return
        await save_user_data(uid, "media_filter", selected)
        language = await language_for(uid)
        text = f"✅ चुना गया: <b>{html.escape(selected)}</b>" if language == "hi" else f"✅ Selected: <b>{html.escape(selected)}</b>"
        await query.message.edit_text(page("🔽 मीडिया फ़िल्टर" if language == "hi" else "🔽 MEDIA FILTER", text), reply_markup=markup([], "filters", language), parse_mode=ParseMode.HTML)
        return
    if action == "toggle_caption":
        data = await get_user_data(uid) or {}
        remove = not data.get("remove_caption", False)
        await save_user_data(uid, "remove_caption", remove)
        language = await language_for(uid)
        text = ("मूल मीडिया कैप्शन हटा दिया जाएगा।" if remove else "मूल मीडिया कैप्शन रखा जाएगा।") if language == "hi" else ("Original media captions will be removed." if remove else "Original media captions will be kept.")
        await query.message.edit_text(page("💬 कैप्शन" if language == "hi" else "💬 CAPTION", text), reply_markup=markup([], "settings", language), parse_mode=ParseMode.HTML)
        return
    requests = {
        "logout_request": "logout",
        "removebot_request": "removebot",
        "reset_request": "reset",
        "delete_request": "delete",
    }
    if action in requests:
        await request_confirmation(query, uid, requests[action])
        return
    if action.startswith("confirm:"):
        from plugins.batch_queue import lock_for
        async with lock_for(uid):
            item = await consume_confirmation(uid, action, query.message.id)
        language = await language_for(uid)
        if not item:
            await query.message.edit_text(page("⚠️ पुष्टि" if language == "hi" else "⚠️ CONFIRM", "पुष्टि समाप्त या अमान्य है। फिर से अनुरोध करें।" if language == "hi" else "Confirmation expired or is invalid. Request it again."), reply_markup=markup([], "home", language), parse_mode=ParseMode.HTML)
            return
        if item["kind"] == "reset":
            await reset_settings(query.message, uid)
        elif item["kind"] in {"logout", "removebot"}:
            await remove_credential(query.message, uid, item["kind"], item)
        else:
            await delete_account_data(query.message, uid)
        return

    # Old inline messages cannot revive removed downloader, premium, payment, or
    # batch interfaces.  Give the user a safe way back instead.
    language = await language_for(uid)
    await query.message.edit_text(
        page("ℹ️ UPDATED DASHBOARD", "This action is no longer available. Use Save Post, Settings, or Account."),
        reply_markup=markup([], "home", language),
        parse_mode=ParseMode.HTML,
    )


async def delete_bot_token_input(message, language):
    """Best-effort deletion; never echo a custom-bot token back to the user."""
    try:
        await message.delete()
    except Exception:
        logger.warning("Could not delete custom bot token input")
        warning = "⚠️ बॉट आपका टोकन संदेश नहीं हटा सका। इसे खुद हटाएँ और token उजागर हो तो BotFather से रद्द करें।" if language == "hi" else "⚠️ The bot could not delete your token message. Delete it yourself and revoke the token with BotFather if exposed."
        await message.reply_text(warning)


async def handle_custom_bot_input(message, uid, token, language):
    from plugins.batch_queue import lock_for
    from plugins.mirror import stop_mirror

    await delete_bot_token_input(message, language)
    cancel = markup([[("❌ CANCEL", "cancel")]], language=language)
    if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,256}", token):
        await message.reply_text("⚠️ टोकन का फ़ॉर्मैट सही नहीं है। फिर भेजें या रद्द करें।" if language == "hi" else "⚠️ Invalid token format. Try again or tap Cancel.", reply_markup=cancel)
        return
    async with lock_for(uid):
        if waiting.get(uid) != "addbot":
            return
        if batch.is_user_active(uid) or get_user_step(uid):
            await message.reply_text("⚠️ पहले चल रही पोस्ट या लॉगिन पूरा करें।" if language == "hi" else "⚠️ Finish the current post or login before replacing the custom bot.", reply_markup=cancel)
            return
        try:
            await stop_mirror(uid, drain=True)
            old = batch.UB.pop(uid, None)
            if old:
                await old.stop()
            for path in (f"user_{uid}.session", f"user_{uid}.session-journal"):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            if not await save_user_bot(uid, token):
                raise RuntimeError("Token storage was not confirmed")
        except Exception:
            logger.warning("Custom bot setup incomplete or uncertain")
            waiting.pop(uid, None)
            input_languages.pop(uid, None)
            body = "टोकन सेव होने की पुष्टि नहीं हुई। अकाउंट स्थिति जाँचें और उजागर टोकन BotFather से रद्द करें।" if language == "hi" else "Could not confirm token storage. Check Account Status and revoke an exposed token with BotFather."
            await message.reply_text(page("⚠️ कस्टम बॉट" if language == "hi" else "⚠️ CUSTOM BOT", body), reply_markup=markup([], "custombot", language), parse_mode=ParseMode.HTML)
            return
        waiting.pop(uid, None)
        input_languages.pop(uid, None)
    body = "✅ टोकन सेव हो गया। इसकी वैधता टेलीग्राम से अभी जाँची नहीं गई है।" if language == "hi" else "✅ Token saved. Telegram validity will be checked when it connects."
    await message.reply_text(page("🤖 कस्टम बॉट" if language == "hi" else "🤖 CUSTOM BOT", body), reply_markup=markup([], "custombot", language), parse_mode=ParseMode.HTML)


def _waiting_filter(_, __, message):
    return bool(message.from_user and message.from_user.id in waiting)


@app.on_message(filters.private & filters.create(_waiting_filter) & (filters.text | filters.photo) & ~filters.command("start"), group=-1)
async def menu_input(_, message):
    uid = message.from_user.id
    action = waiting.get(uid)
    text = (message.text or "").strip()
    if uid in busy_inputs:
        if action == "addbot":
            await delete_bot_token_input(message, input_languages.get(uid, "en"))
        await message.reply_text("⏳ Your previous input is still being processed. Please wait.")
        message.stop_propagation()
        return

    marker = object()
    busy_inputs[uid] = marker
    try:
        language = await language_for(uid)
        if action == "addbot":
            await handle_custom_bot_input(message, uid, text, input_languages.get(uid, language))
            return
        if action == "mirror":
            if not re.fullmatch(r"(?:@[A-Za-z0-9_]{4,32}|-?\d{5,20})", text):
                await message.reply_text("⚠️ @username या संख्यात्मक चैट ID भेजें।" if language == "hi" else "⚠️ Send @username or a numeric chat ID.", reply_markup=markup([[("❌ CANCEL", "cancel")]], language=language))
                return
            from plugins.batch_queue import lock_for
            from plugins.mirror import start_mirror
            source = int(text) if re.fullmatch(r"-?\d+", text) else text
            async with lock_for(uid):
                if waiting.get(uid) != "mirror":
                    return
                try:
                    ok, reason = await start_mirror(uid, source)
                except Exception:
                    logger.warning("Clone start result uncertain")
                    ok, reason = False, "Could not confirm clone start. Check Clone Status or tap Stop Clone."
                waiting.pop(uid, None)
            title = "🪞 लाइव क्लोन" if language == "hi" else "🪞 LIVE CLONE"
            await message.reply_text(page(title, html.escape(localization.translate(reason, language))), reply_markup=markup([[("📊 CLONE STATUS", "mirror_status")]], "save", language), parse_mode=ParseMode.HTML)
            return
        if action == "destination":
            if not re.fullmatch(r"-?\d+(?:/\d+)?", text):
                await message.reply_text("⚠️ संख्यात्मक चैट ID या chat ID/topic ID भेजें।" if language == "hi" else "⚠️ Send a numeric chat ID or chat ID/topic ID.")
                return
            from plugins.mirror import active as active_mirrors
            clone = active_mirrors.get(uid)
            if clone and clone["source_id"] == int(text.split("/", 1)[0]):
                await message.reply_text("⚠️ डेस्टिनेशन क्लोन का स्रोत नहीं हो सकता। पहले क्लोन रोकें।" if language == "hi" else "⚠️ Destination cannot be the clone source. Stop the clone first.")
                return
            await save_user_data(uid, "chat_id", text)
        elif action == "caption":
            if not text or len(text) > 1024:
                await message.reply_text("⚠️ 1 से 1024 अक्षर का कैप्शन भेजें।" if language == "hi" else "⚠️ Send caption text between 1 and 1024 characters.")
                return
            await save_user_data(uid, "caption", text)
        else:
            return
        waiting.pop(uid, None)
        await message.reply_text(page("✅ सेव हो गया" if language == "hi" else "✅ SAVED", "आपकी सेटिंग अपडेट हो गई।" if language == "hi" else "Your setting has been updated."), reply_markup=markup([], "settings", language), parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Focused menu input failed for action %s", action)
        waiting.pop(uid, None)
        input_languages.pop(uid, None)
        language = await language_for(uid)
        await message.reply_text(page("⚠️ त्रुटि" if language == "hi" else "⚠️ ERROR", "यह कार्रवाई पूरी नहीं हो सकी। फिर कोशिश करें।" if language == "hi" else "Could not complete this action. Please try again."), reply_markup=markup([], "home", language), parse_mode=ParseMode.HTML)
    finally:
        if busy_inputs.get(uid) is marker:
            busy_inputs.pop(uid, None)
        message.stop_propagation()

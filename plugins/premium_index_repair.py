"""Owner-reviewed removal of a single TTL index; never modify premium records.

MongoDB cannot atomically compare an index definition and drop it by name.
Operators must stop old workers and all other index/schema changes first.
"""
import asyncio
import copy
import hashlib
import html
import logging
import re
import secrets
import time
from datetime import datetime

from bson import json_util
from pyrogram.enums import ParseMode
from config import OWNER_ID
from plugins.batch_queue import lock_for
from utils.func import db, premium_users_collection

logger = logging.getLogger(__name__)
MAX_INDEXES = 10
CONFIRM_SECONDS = 600
states = {}
# Serialize index rechecks/drops across owners in this worker.
repair_lock = asyncio.Lock()


def clear(owner_id):
    """Caller holds the user's menu lock."""
    states.pop(owner_id, None)


def _authorized(owner_id):
    return owner_id in OWNER_ID


def _serialize(spec):
    return json_util.dumps(spec, sort_keys=True, ensure_ascii=True)


def _reviewable(name, spec):
    if (not isinstance(name, str) or name == '_id_' or not name or len(name.encode('utf-8')) > 128
            or any(ord(c) < 32 or ord(c) == 127 for c in name) or not isinstance(spec, dict)
            or 'expireAfterSeconds' not in spec):
        return False
    try:
        return len(_serialize(spec)) <= 1200
    except Exception:
        return False


def _valid_state(item, kind, token, message_id):
    return bool(item and item['kind'] == kind and item['message_id'] == message_id
                and item['expires'] >= time.monotonic() and isinstance(token, str)
                and re.fullmatch(r'[0-9a-f]{16}', token))


def _text(language, en, hi):
    return hi if language == 'hi' else en


async def show(message, owner_id, language='en'):
    from plugins.menu import markup, page
    if not _authorized(owner_id):
        return
    async with lock_for(owner_id):
        clear(owner_id)
        # Mark the in-progress read so navigation waits for it and invalidates
        # any choices it produces, rather than leaving a hidden live button.
        states[owner_id] = {'kind': 'loading'}
        try:
            indexes = await premium_users_collection.index_information()
            ttl = [(name, spec) for name, spec in indexes.items()
                   if isinstance(spec, dict) and 'expireAfterSeconds' in spec]
            reviewable = [(name, copy.deepcopy(spec)) for name, spec in ttl if _reviewable(name, spec)]
        except Exception:
            clear(owner_id)
            logger.warning('Could not inspect premium TTL indexes')
            body = _text(language, 'Could not inspect premium_users indexes. Check database access and permissions. No changes were made.',
                         'premium_users indexes जाँचे नहीं जा सके। डेटाबेस access और permissions जाँचें। कोई बदलाव नहीं हुआ।')
            await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner', language), parse_mode=ParseMode.HTML)
            return
        rows = []
        entries = {}
        for name, spec in reviewable[:MAX_INDEXES]:
            token = secrets.token_hex(8)
            entries[token] = (name, spec)
            rows.append([(f'🔍 {name[:36]}', 'owner_ttl_select:' + token)])
        if entries:
            states[owner_id] = {'kind': 'choices', 'entries': entries, 'message_id': message.id,
                                'expires': time.monotonic() + CONFIRM_SECONDS}
        else:
            clear(owner_id)
        body = _text(language,
            f'Found {len(ttl)} TTL index(es) on premium_users. Select one to inspect; nothing is removed automatically. '
            'Before removal stop old workers, pause payment/webhook processing and concurrent migrations, back up the database and reconcile missing payment history. '
            'Removal does not restore deleted records.',
            f'premium_users पर {len(ttl)} TTL index मिले। जाँचने के लिए चुनें; कुछ भी अपने आप नहीं हटेगा। '
            'हटाने से पहले पुराने workers, payment/webhook processing और concurrent migrations रोकें, डेटाबेस का बैकअप लें और missing payment history जाँचें। '
            'Index हटाने से पहले से मिटे रिकॉर्ड वापस नहीं आएँगे।')
        omitted = len(ttl) - len(entries)
        if omitted:
            body += _text(language, f'\n\n⚠️ {omitted} index(es) are not listed; review them manually.',
                          f'\n\n⚠️ {omitted} index यहाँ नहीं दिखे; उन्हें मैन्युअली जाँचें।')
        try:
            await message.edit_text(page('🛠️ 𝗧𝗧𝗟 𝗥𝗘𝗩𝗜𝗘' if language != 'hi' else '🛠️ TTL जाँच', body),
                                    reply_markup=markup(rows, 'owner', language), parse_mode=ParseMode.HTML)
        except Exception:
            clear(owner_id)
            raise


async def select(message, owner_id, token, language='en'):
    from plugins.menu import markup, page
    if not _authorized(owner_id):
        return
    async with lock_for(owner_id):
        item = states.get(owner_id)
        choice = item['entries'].get(token) if _valid_state(item, 'choices', token, message.id) else None
        if not choice:
            body = _text(language, 'Selection expired or invalid. Inspect indexes again.', 'चयन समाप्त या अमान्य है। Index दोबारा जाँचें।')
            await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)
            return
        name, expected = choice
        states[owner_id] = {'kind': 'loading'}
        try:
            current = (await premium_users_collection.index_information()).get(name)
        except Exception:
            logger.warning('Could not recheck premium TTL index')
            current = None
        if current != expected or not _reviewable(name, current):
            clear(owner_id)
            body = _text(language, 'Index changed or could not be checked. No index was removed. Inspect again.',
                         'Index बदल गया या जाँचा नहीं जा सका। कोई index नहीं हटाया गया। फिर जाँचें।')
            await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)
            return
        token = secrets.token_hex(8)
        states[owner_id] = {'kind': 'confirm', 'token': token, 'name': name, 'spec': expected,
                            'message_id': message.id, 'expires': time.monotonic() + CONFIRM_SECONDS}
        info = html.escape(_serialize(expected))
        name_text = html.escape(name)
        body = _text(language,
            f'Index: <code>{name_text}</code>\nDefinition: <code>{info}</code>\n\n'
            'Only remove this TTL index after stopping old workers, pausing payment/webhook processing and all concurrent index/schema changes, backing up the database, and reviewing lost payment history. '
            'Removing the index stops its automatic expiry deletion; previously deleted records will not return. '
            'This checks the definition before dropping, but that check and drop are not atomic. Continue?',
            f'Index: <code>{name_text}</code>\nविवरण: <code>{info}</code>\n\n'
            'केवल पुराने workers, payment/webhook processing और concurrent index/schema बदलाव रोकने, डेटाबेस का बैकअप लेने और गुम payment history जाँचने के बाद यह TTL index हटाएँ। '
            'इसे हटाने पर इसकी automatic expiry deletion रुकेगी; पहले मिटे रिकॉर्ड वापस नहीं आएँगे। '
            'हटाने से पहले जाँच और removal एक atomic operation नहीं हैं। आगे बढ़ें?')
        try:
            await message.edit_text(page('⚠️ पुष्टि करें' if language == 'hi' else '⚠️ 𝗖𝗢𝗡𝗙𝗜𝗥𝗠', body),
                reply_markup=markup([[('✅ 𝗥𝗘𝗠𝗢𝗩𝗘 𝗧𝗧𝗟', 'owner_ttl_drop:' + token), ('❌ 𝗖𝗔𝗡𝗖𝗘𝗟', 'owner_ttl')]], language=language), parse_mode=ParseMode.HTML)
        except Exception:
            clear(owner_id)
            raise


async def drop(message, owner_id, token, language='en'):
    from plugins.menu import markup, page
    if not _authorized(owner_id):
        return
    async with lock_for(owner_id):
        item = states.get(owner_id)
        valid = (_valid_state(item, 'confirm', token, message.id)
                 and secrets.compare_digest(item['token'], token))
        if not valid:
            body = _text(language, 'Confirmation expired or invalid. Inspect indexes again.', 'पुष्टि समाप्त या अमान्य है। Index दोबारा जाँचें।')
            await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)
            return
        clear(owner_id)  # one-shot, even on failure or a duplicate callback
        name, spec = item['name'], item['spec']
        async with repair_lock:
            try:
                current = (await premium_users_collection.index_information()).get(name)
            except Exception:
                logger.warning('Could not check TTL index before removal')
                current = None
            if current != spec or not _reviewable(name, current):
                body = _text(language, 'Index changed or could not be checked. No index was removed. Inspect again.',
                             'Index बदल गया या जाँचा नहीं जा सका। कोई index नहीं हटाया गया। फिर जाँचें।')
                await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)
                return
            audit = db['admin_audit']
            fingerprint = hashlib.sha256(_serialize(spec).encode('utf-8')).hexdigest()
            try:
                record = await audit.insert_one({'owner_id': owner_id, 'action': 'premium_ttl_remove',
                    'index_name': name, 'index_fingerprint': fingerprint,
                    'created_at': datetime.utcnow(), 'status': 'processing'})
                if not record.acknowledged or record.inserted_id is None:
                    raise RuntimeError('Audit insert not acknowledged')
            except Exception:
                logger.warning('Could not record TTL removal audit')
                body = _text(language, 'Audit could not be confirmed. No index removal was attempted. Check database permissions.',
                             'Audit की पुष्टि नहीं हुई। Index हटाने का प्रयास नहीं किया गया। डेटाबेस permissions जाँचें।')
                await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)
                return
            try:
                await premium_users_collection.drop_index(name)
                indexes = await premium_users_collection.index_information()
                if name in indexes:
                    raise RuntimeError('Index still exists')
            except Exception:
                logger.warning('TTL removal outcome uncertain; audit %s', record.inserted_id)
                body = _text(language, 'Removal could not be confirmed. The index may already be removed. Inspect indexes and the audit before retrying; do not grant until the history is reconciled.',
                             'Index हटने की पुष्टि नहीं हुई; यह हट चुका हो सकता है। दोबारा कोशिश से पहले indexes और audit जाँचें; history जाँचने से पहले grant न करें।')
                await message.edit_text(page('⚠️ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)
                return
            try:
                result = await audit.update_one({'_id': record.inserted_id, 'owner_id': owner_id, 'action': 'premium_ttl_remove'},
                    {'$set': {'status': 'completed', 'completed_at': datetime.utcnow()}})
                audit_ok = bool(result.acknowledged and result.matched_count == 1)
            except Exception:
                audit_ok = False
            remaining = sum(1 for value in indexes.values() if isinstance(value, dict) and 'expireAfterSeconds' in value)
            body = _text(language,
                f'✅ Index <code>{html.escape(name)}</code> removed. {remaining} TTL index(es) remain on premium_users. '
                'Deleted history is not restored; reconcile it before billing or retrying an uncertain grant.',
                f'✅ Index <code>{html.escape(name)}</code> हटाया गया। premium_users पर {remaining} TTL index बाकी हैं। '
                'पुरानी मिट चुकी history वापस नहीं आई; billing या अनिश्चित grant दोहराने से पहले जाँचें।')
            if not audit_ok:
                body += _text(language, ' ⚠️ Audit completion could not be confirmed; review the audit.',
                              ' ⚠️ Audit पूरा होने की पुष्टि नहीं हुई; audit जाँचें।')
            await message.edit_text(page('✅ TTL', body), reply_markup=markup([], 'owner_ttl', language), parse_mode=ParseMode.HTML)

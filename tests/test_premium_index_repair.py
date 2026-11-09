"""Offline tests for explicit, scoped, audited TTL-index removal."""
import asyncio
import copy
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, menu, owner_grant, premium_index_repair as repair
from utils.billing import UnsafePremiumIndexError, check_premium_indexes
from utils.custom_filters import user_steps


class Indexes:
    def __init__(self):
        self.indexes = {'_id_': {'key': [('_id', 1)]},
                        'user_id_1': {'key': [('user_id', 1)], 'unique': True},
                        'expireAt_1': {'key': [('expireAt', 1)], 'expireAfterSeconds': 0}}
        self.documents = [{'user_id': 456, 'applied_payments': ['paid-1']}]
        self.drop_index = AsyncMock(side_effect=self._drop)
        self.index_information = AsyncMock(side_effect=self._list)

    async def _list(self):
        return copy.deepcopy(self.indexes)

    async def _drop(self, name):
        del self.indexes[name]


class Audit:
    def __init__(self):
        self.insert_one = AsyncMock(return_value=SimpleNamespace(acknowledged=True, inserted_id='audit-1'))
        self.update_one = AsyncMock(return_value=SimpleNamespace(acknowledged=True, matched_count=1))


def callback(action, uid=123, message_id=100):
    message = SimpleNamespace(id=message_id, chat=SimpleNamespace(id=uid, type=SimpleNamespace(value='private')),
                              edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), message=message, data='ui:' + action, answer=AsyncMock())


def first_callback(msg):
    return msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data


class TTLRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        repair.states.clear(); repair.repair_lock = asyncio.Lock()
        menu.waiting.clear(); menu.busy_inputs.clear(); batch.Z.clear(); batch.ACTIVE_USERS.clear()
        batch_queue.locks.clear(); user_steps.clear()
        self.indexes = Indexes(); self.audit = Audit()
        self.patches = [patch.object(menu, 'OWNER_ID', [123, 456]), patch.object(repair, 'OWNER_ID', [123, 456]),
                        patch.object(repair, 'premium_users_collection', self.indexes),
                        patch.object(repair, 'db', {'admin_audit': self.audit}),
                        patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en')]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    async def inspect(self, uid=123, message_id=100):
        q = callback('owner_ttl', uid, message_id)
        await menu.menu_callback(None, q)
        q.data = first_callback(q.message)
        return q

    async def confirmation(self, uid=123, message_id=100):
        q = await self.inspect(uid, message_id)
        self.assertTrue(q.data.startswith('ui:owner_ttl_select:'))
        await menu.menu_callback(None, q)
        self.assertIn('not atomic', q.message.edit_text.await_args.args[0])
        self.assertIn('backing up', q.message.edit_text.await_args.args[0])
        q.data = first_callback(q.message)
        self.assertTrue(q.data.startswith('ui:owner_ttl_drop:'))
        return q

    async def test_inspection_and_selection_do_not_mutate_or_drop_data(self):
        q = await self.confirmation()
        self.assertIn('expireAt_1', q.message.edit_text.await_args.args[0])
        self.assertLessEqual(len(q.data.encode()), 64)
        self.indexes.drop_index.assert_not_awaited(); self.audit.insert_one.assert_not_awaited()
        check_premium_indexes({'_id_': {}})
        with self.assertRaises(UnsafePremiumIndexError): check_premium_indexes(self.indexes.indexes)

    async def test_confirm_removes_only_reviewed_ttl_and_audits(self):
        q = await self.confirmation(); await menu.menu_callback(None, q)
        self.indexes.drop_index.assert_awaited_once_with('expireAt_1')
        self.assertIn('user_id_1', self.indexes.indexes); self.assertIn('_id_', self.indexes.indexes)
        self.assertEqual(self.indexes.documents, [{'user_id': 456, 'applied_payments': ['paid-1']}])
        check_premium_indexes(self.indexes.indexes)
        audit = self.audit.insert_one.await_args.args[0]
        self.assertEqual(audit['owner_id'], 123); self.assertEqual(audit['action'], 'premium_ttl_remove')
        self.assertEqual(audit['index_name'], 'expireAt_1'); self.assertEqual(len(audit['index_fingerprint']), 64)
        self.audit.update_one.assert_awaited_once()
        self.assertIn('0 TTL', q.message.edit_text.await_args.args[0])
        self.assertIn('not restored', q.message.edit_text.await_args.args[0])
        await menu.menu_callback(None, q)
        self.indexes.drop_index.assert_awaited_once()

    async def test_remaining_ttl_indexes_keep_billing_guard_closed(self):
        self.indexes.indexes['other_ttl'] = {'key': [('other', 1)], 'expireAfterSeconds': 3600}
        q = await self.confirmation(); await menu.menu_callback(None, q)
        self.assertIn('1 TTL', q.message.edit_text.await_args.args[0])
        with self.assertRaises(UnsafePremiumIndexError): check_premium_indexes(self.indexes.indexes)

    async def test_forged_foreign_group_and_other_message_cannot_drop(self):
        q = await self.confirmation()
        forged = callback('owner_ttl_drop:' + 'a' * 16); await menu.menu_callback(None, forged)
        foreign = callback(q.data[3:], uid=456, message_id=100); await menu.menu_callback(None, foreign)
        wrong_message = callback(q.data[3:], message_id=101); await menu.menu_callback(None, wrong_message)
        not_owner = callback(q.data[3:], uid=789); await menu.menu_callback(None, not_owner)
        self.assertTrue(not_owner.answer.await_args.kwargs['show_alert'])
        group = callback(q.data[3:]); group.message.chat.type.value = 'supergroup'; await menu.menu_callback(None, group)
        self.assertTrue(group.answer.await_args.kwargs['show_alert'])
        self.indexes.drop_index.assert_not_awaited(); self.audit.insert_one.assert_not_awaited()
        self.assertIn(123, repair.states)

    async def test_expiration_and_navigation_prevent_old_confirmation(self):
        q = await self.confirmation(); repair.states[123]['expires'] = time.monotonic() - 1
        await menu.menu_callback(None, q); self.indexes.drop_index.assert_not_awaited()
        q = await self.confirmation(); await menu.menu_callback(None, callback('home'))
        await menu.menu_callback(None, q); self.indexes.drop_index.assert_not_awaited()

    async def test_index_changes_before_selection_or_confirmation_rejected(self):
        q = await self.inspect(); self.indexes.indexes['expireAt_1']['expireAfterSeconds'] = 30
        await menu.menu_callback(None, q)
        self.assertIn('changed', q.message.edit_text.await_args.args[0]); self.indexes.drop_index.assert_not_awaited()
        q = await self.confirmation(); self.indexes.indexes['expireAt_1']['unique'] = True
        await menu.menu_callback(None, q)
        self.assertIn('changed', q.message.edit_text.await_args.args[0]); self.indexes.drop_index.assert_not_awaited()

    async def test_no_ttl_and_unreviewable_indexes_never_offer_remove(self):
        self.indexes.indexes.pop('expireAt_1')
        q = callback('owner_ttl'); await menu.menu_callback(None, q)
        self.assertIn('Found 0 TTL', q.message.edit_text.await_args.args[0])
        self.assertNotIn(123, repair.states)
        self.indexes.indexes['bad\nname'] = {'key': [('x', 1)], 'expireAfterSeconds': 1}
        await menu.menu_callback(None, q)
        self.assertIn('not listed', q.message.edit_text.await_args.args[0])
        self.indexes.drop_index.assert_not_awaited()

    async def test_audit_failure_stops_removal_and_db_failure_is_uncertain(self):
        self.audit.insert_one.side_effect = RuntimeError('private db info')
        q = await self.confirmation(); await menu.menu_callback(None, q)
        self.assertIn('No index removal was attempted', q.message.edit_text.await_args.args[0])
        self.indexes.drop_index.assert_not_awaited()
        self.audit.insert_one.side_effect = None
        async def ambiguous(name):
            del self.indexes.indexes[name]
            raise RuntimeError('private db info')
        self.indexes.drop_index.side_effect = ambiguous
        q = await self.confirmation(); await menu.menu_callback(None, q)
        result = q.message.edit_text.await_args.args[0]
        self.assertIn('may already be removed', result); self.assertNotIn('private db info', result)
        self.audit.update_one.assert_not_awaited()

    async def test_completion_audit_failure_does_not_hide_removed_index(self):
        self.audit.update_one.side_effect = RuntimeError('db')
        q = await self.confirmation(); await menu.menu_callback(None, q)
        self.assertIn('removed', q.message.edit_text.await_args.args[0])
        self.assertIn('Audit completion could not be confirmed', q.message.edit_text.await_args.args[0])

    async def test_concurrent_duplicate_confirmations_drop_only_once(self):
        q = await self.confirmation(); entered, release = asyncio.Event(), asyncio.Event()
        async def drop(name):
            entered.set(); await release.wait(); del self.indexes.indexes[name]
        self.indexes.drop_index.side_effect = drop
        a = asyncio.create_task(menu.menu_callback(None, q)); await entered.wait()
        b = asyncio.create_task(menu.menu_callback(None, q)); await asyncio.sleep(0)
        release.set(); await asyncio.gather(a, b)
        self.indexes.drop_index.assert_awaited_once(); self.audit.insert_one.assert_awaited_once()

    async def test_different_owners_serialized_and_second_revalidates(self):
        q1, q2 = await self.confirmation(123, 100), await self.confirmation(456, 101)
        entered, release = asyncio.Event(), asyncio.Event()
        async def drop(name):
            entered.set(); await release.wait(); del self.indexes.indexes[name]
        self.indexes.drop_index.side_effect = drop
        a = asyncio.create_task(menu.menu_callback(None, q1)); await entered.wait()
        b = asyncio.create_task(menu.menu_callback(None, q2)); await asyncio.sleep(0)
        release.set(); await asyncio.gather(a, b)
        self.indexes.drop_index.assert_awaited_once(); self.audit.insert_one.assert_awaited_once()
        self.assertIn('changed', q2.message.edit_text.await_args.args[0])

    async def test_unacknowledged_audit_and_failed_readback(self):
        self.audit.insert_one.return_value = SimpleNamespace(acknowledged=False, inserted_id='audit-1')
        q = await self.confirmation(); await menu.menu_callback(None, q)
        self.assertIn('No index removal was attempted', q.message.edit_text.await_args.args[0])
        self.indexes.drop_index.assert_not_awaited()
        self.audit.insert_one.return_value = SimpleNamespace(acknowledged=True, inserted_id='audit-2')
        q = await self.confirmation()
        old_drop = self.indexes._drop
        async def drop_then_break_readback(name):
            await old_drop(name)
            self.indexes.index_information.side_effect = RuntimeError('readback failed')
        self.indexes.drop_index.side_effect = drop_then_break_readback
        await menu.menu_callback(None, q)
        self.assertIn('may already be removed', q.message.edit_text.await_args.args[0])
        self.audit.update_one.assert_not_awaited()

    async def test_navigation_during_selection_invalidates_confirmation(self):
        q = await self.inspect()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_read():
            entered.set(); await release.wait(); return copy.deepcopy(self.indexes.indexes)
        self.indexes.index_information.side_effect = slow_read
        a = asyncio.create_task(menu.menu_callback(None, q)); await entered.wait()
        b = asyncio.create_task(menu.menu_callback(None, callback('home'))); await asyncio.sleep(0)
        release.set(); await asyncio.gather(a, b)
        self.assertNotIn(123, repair.states)
        self.indexes.drop_index.assert_not_awaited()

    async def test_navigation_during_initial_inspection_invalidates_choices(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_read():
            entered.set(); await release.wait(); return copy.deepcopy(self.indexes.indexes)
        self.indexes.index_information.side_effect = slow_read
        a = asyncio.create_task(menu.menu_callback(None, callback('owner_ttl'))); await entered.wait()
        b = asyncio.create_task(menu.menu_callback(None, callback('home'))); await asyncio.sleep(0)
        release.set(); await asyncio.gather(a, b)
        self.assertNotIn(123, repair.states)
        self.indexes.drop_index.assert_not_awaited()

    async def test_hindi_and_untrusted_index_details_are_escaped(self):
        with patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi'):
            self.indexes.indexes = {'<ttl>': {'key': [('<danger>', 1)], 'expireAfterSeconds': 0}}
            q = await self.inspect(); await menu.menu_callback(None, q)
            text = q.message.edit_text.await_args.args[0]
            self.assertIn('&lt;ttl&gt;', text); self.assertIn('&lt;danger&gt;', text)
            self.assertNotIn('<danger>', text); self.assertIn('बैकअप', text)
            self.assertIn('TTL index हटाएँ', q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].text)

    async def test_grant_ttl_error_links_to_review_without_changing_data(self):
        msg = SimpleNamespace(reply_text=AsyncMock())
        with patch.object(owner_grant, 'premium_users_collection', self.indexes):
            self.assertTrue(await owner_grant.apply_owner_grant(None, msg, 123, '456 30'))
        self.assertEqual(msg.reply_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'ui:owner_ttl')
        self.indexes.drop_index.assert_not_awaited()

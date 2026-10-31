"""Marks that reach the mail server and say so (Will, October 31, 2026).

Will: a message marked read in Voice Companion stayed unread in Gmail until Thunderbird was
restarted. "Make sure everything is working absolutely seamlessly across the board": read,
unread, flags, importance, read receipts, sending with high importance, Gmail and Microsoft.
The add-on side is tested in thunderbird_mail_extension/test-marks.cjs, and with a real
Thunderbird on Windows by check_thunderbird_marks_live.py.
"""
import tempfile
import unittest
from unittest.mock import patch

from mail_voice import MailSession, status_words


class Body(str):
    pass


def opened(text, **parts):
    body = Body(text)
    body.attachments = []
    body.marked_read = parts.get('marked_read', True)
    body.read_not_confirmed = parts.get('read_not_confirmed', False)
    body.conversation_ids = parts.get('conversation_ids', [])
    body.receipt = parts.get('receipt', '')
    return body


class FakeClient:
    """Thunderbird through the add-on, as ThunderbirdMailbox answers."""
    server = 'confirmed'
    conversation = []
    body = None
    important_error = None

    def __init__(self, *args):
        self.calls = []
        FakeClient.last = self

    def folders(self):
        return [('Inbox', 'INBOX')]

    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': 3, 'from': 'noreply@uber.com', 'subject': 'Your Saturday morning trip with Uber', 'unread': True},
                 {'id': 2, 'from': 'ann@example.com', 'subject': 'Lunch', 'unread': True},
                 {'id': 1, 'from': 'noreply@uber.com', 'subject': 'Your Saturday morning trip with Uber', 'unread': True}],
                None)

    def mark(self, ids, read=None, flagged=None):
        self.calls.append(('mark', ids, read, flagged))
        return {'changed': len(ids), 'missing': 0, 'this_computer_only': False, 'server': self.server,
                'conversation_ids': list(self.conversation)}

    def read_message(self, message_id, folder_id=None):
        self.calls.append(('read', message_id))
        return self.body or opened('The trip receipt.')

    def receipt_answer(self, message_id, send):
        self.calls.append(('receipt', message_id, send))
        return True

    def important(self, message_id, on):
        self.calls.append(('important', message_id, on))
        if self.important_error:
            from mailbox_access import MailboxError
            raise MailboxError(self.important_error)
        return {'done': True, 'already': False}


class MailSyncTests(unittest.TestCase):
    def setUp(self):
        FakeClient.server = 'confirmed'
        FakeClient.conversation = []
        FakeClient.body = None
        FakeClient.important_error = None
        self.folder = tempfile.TemporaryDirectory()
        patcher = patch('mail_voice.account_token', return_value=('willwalsh14@gmail.com', 'token'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.folder.cleanup)
        self.session = MailSession('thunderbird', self.folder.name, FakeClient)
        self.session.process('list messages')

    def rows(self):
        return {row['id']: row for row in self.session.rows}

    def test_marking_read_marks_the_whole_gmail_conversation_in_the_list(self):
        FakeClient.conversation = [1]
        self.assertEqual(self.session.process('mark as read'), 'Marked as read.')
        self.assertEqual(FakeClient.last.calls[-1], ('mark', [3], True, None))
        self.assertFalse(self.rows()[3]['unread'])
        self.assertFalse(self.rows()[1]['unread'], 'the other message of the conversation')
        self.assertTrue(self.rows()[2]['unread'], 'another conversation stays unread')

    def test_marking_unread_does_not_touch_the_conversation(self):
        FakeClient.conversation = [1]
        self.session.process('mark as unread')
        self.assertTrue(self.rows()[1]['unread'])

    def test_a_change_the_server_did_not_confirm_is_said(self):
        FakeClient.server = 'failed'
        said = self.session.process('mark as read')
        self.assertTrue(said.startswith('Marked as read. Your email provider did not confirm the change'), said)
        self.assertIn('Check your internet connection, then try again.', said)
        FakeClient.server = 'offline'
        self.assertEqual(self.session.process('flag'),
                         'Flagged. Thunderbird is offline. It sends the change when it is back online.')
        # Confirmed and Microsoft's "sent": nothing more is said.
        for state in ('confirmed', 'sent', ''):
            FakeClient.server = state
            self.assertEqual(self.session.process('remove flag'), 'Flag removed.')

    def test_opening_with_enter_marks_the_conversation_read_and_says_when_not_confirmed(self):
        FakeClient.body = opened('The trip receipt.', conversation_ids=[1])
        said = self.session.process('open message')
        self.assertTrue(said.startswith('Message opened. The trip receipt.'), said)
        self.assertFalse(self.rows()[3]['unread'])
        self.assertFalse(self.rows()[1]['unread'])
        self.assertTrue(self.rows()[2]['unread'])
        self.session.process('back to folder')
        FakeClient.body = opened('Lunch at noon.', read_not_confirmed=True)
        self.session.process('next message')
        said = self.session.process('open message')
        self.assertTrue(said.startswith('Message opened. Your email provider did not confirm it as read yet. Lunch'), said)

    def test_a_read_receipt_is_asked_about_before_the_message_is_read(self):
        FakeClient.body = opened('Please confirm you got this.', receipt='ask')
        said = self.session.process('open message')
        self.assertEqual(said, 'Message opened. The sender asked for a read receipt. Send it? Say yes or no.')
        self.assertNotIn('Please confirm', said)
        said = self.session.process('yes')
        self.assertTrue(said.startswith('Read receipt sent. Please confirm you got this.'), said)
        self.assertEqual(FakeClient.last.calls[-1], ('receipt', 3, True))
        self.assertIsNone(self.session.pending)

    def test_no_read_receipt_and_other_commands_leave_it_undecided(self):
        FakeClient.body = opened('Please confirm.', receipt='ask')
        self.session.process('open message')
        said = self.session.process('no')
        self.assertTrue(said.startswith('No read receipt sent. Please confirm.'), said)
        self.assertEqual(FakeClient.last.calls[-1], ('receipt', 3, False))
        self.session.process('back to folder')
        self.session.process('open message')
        self.session.process('message status')
        self.assertIsNone(self.session.pending)
        self.assertNotIn('receipt', [call[0] for call in FakeClient.last.calls[-2:]])

    def test_a_receipt_thunderbird_handled_by_its_settings_is_not_asked(self):
        FakeClient.body = opened('Hello.', receipt='handled')
        self.assertTrue(self.session.process('open message').startswith('Message opened. Hello.'))
        self.assertIsNone(self.session.pending)

    def test_gmail_important_marker(self):
        self.assertEqual(self.session.process('mark as important'), 'Marked important.')
        self.assertEqual(FakeClient.last.calls[-1], ('important', 3, True))
        self.assertTrue(self.rows()[3]['important'])
        self.assertIn('important', self.session.process('message status'))
        # H turns it over.
        self.assertEqual(self.session.process('mark important or not important'), 'Not marked important now.')
        self.assertEqual(FakeClient.last.calls[-1], ('important', 3, False))
        self.assertEqual(self.session.process('remove important'), 'Not marked important now.')

    def test_important_on_a_microsoft_account_says_why_not(self):
        FakeClient.important_error = ('Only Gmail accounts have an Important marker you can change. '
                                      'Others show the importance the sender chose.')
        said = self.session.process('mark as important')
        self.assertIn('Only Gmail accounts have an Important marker', said)
        self.assertNotIn('important', self.rows()[3])

    def test_status_words_say_important(self):
        self.assertEqual(status_words({'unread': True, 'important': True}), 'Unread, important')
        self.assertEqual(status_words({'important': False}), '')

    def test_important_key_and_menu(self):
        from input_parity import SCREEN_KEYS
        from screen_menus import MENUS
        self.assertEqual(SCREEN_KEYS['mailbox']['H'], 'mark important or not important')
        items = [item for _, group in MENUS['mailbox'][1] for item in group]
        self.assertIn(('Important or not important (Gmail)', 'H', 'mark important or not important'), items)


class ThunderbirdMailboxSyncTests(unittest.TestCase):
    """What ThunderbirdMailbox passes on from the add-on."""

    def test_mark_passes_the_server_answer_and_the_conversation(self):
        import thunderbird_mailbox
        from thunderbird_mailbox import ThunderbirdMailbox
        answers = {'mark': {'changed': 1, 'missing': 0, 'server': 'failed', 'conversationIds': [4, 'x', 5]}}
        with patch.object(thunderbird_mailbox, 'call', side_effect=lambda method, **params: answers[method]):
            box = ThunderbirdMailbox('me@gmail.com', {})
            self.assertEqual(box.mark([3], read=True), {'changed': 1, 'missing': 0, 'this_computer_only': False,
                                                       'server': 'failed', 'conversation_ids': [4, 5]})
            answers['mark'] = {'changed': 1, 'missing': 0, 'server': 'something new'}
            self.assertEqual(box.mark([3], read=True)['server'], '')
            answers['mark'] = {'changed': 1, 'missing': 0}   # an older add-on
            self.assertEqual(box.mark([3], read=True)['conversation_ids'], [])

    def test_read_passes_the_receipt_and_the_conversation(self):
        import thunderbird_mailbox
        from thunderbird_mailbox import ThunderbirdMailbox
        answer = {'plain': 'Hello', 'read': True, 'readNotConfirmed': True, 'conversationIds': [8], 'receipt': 'ask'}
        with patch.object(thunderbird_mailbox, 'call', return_value=answer):
            body = ThunderbirdMailbox('me@gmail.com', {}).read_message(3)
        self.assertTrue(body.marked_read)
        self.assertTrue(body.read_not_confirmed)
        self.assertEqual(body.conversation_ids, [8])
        self.assertEqual(body.receipt, 'ask')
        answer = {'plain': 'Hello', 'read': True, 'receipt': 'weird'}
        with patch.object(thunderbird_mailbox, 'call', return_value=answer):
            body = ThunderbirdMailbox('me@gmail.com', {}).read_message(3)
        self.assertEqual(body.receipt, '')
        self.assertFalse(body.read_not_confirmed)

    def test_important_and_receipt_answer(self):
        import thunderbird_mailbox
        from mailbox_access import MailboxError
        from thunderbird_mailbox import ThunderbirdMailbox, OLD_ADDON_IMPORTANT
        calls = []

        def fake(method, **params):
            calls.append((method, params))
            return {'important': {'done': True, 'already': True}, 'receiptAnswer': {'done': True}}[method]
        with patch.object(thunderbird_mailbox, 'call', side_effect=fake):
            box = ThunderbirdMailbox('me@gmail.com', {})
            self.assertEqual(box.important('3', True), {'done': True, 'already': True})
            self.assertTrue(box.receipt_answer(3, False))
        self.assertEqual(calls, [('important', {'messageId': 3, 'on': True}), ('receiptAnswer', {'messageId': 3, 'send': False})])
        with patch.object(thunderbird_mailbox, 'call', side_effect=MailboxError('Unsupported mail request.')):
            with self.assertRaises(MailboxError) as raised:
                ThunderbirdMailbox('me@gmail.com', {}).important(3, True)
        self.assertEqual(str(raised.exception), OLD_ADDON_IMPORTANT)

    def test_the_list_carries_gmails_important_marker(self):
        from thunderbird_mailbox import ThunderbirdMailbox
        rows, _ = ThunderbirdMailbox._rows({'rows': [{'id': 1, 'from': 'a@b', 'important': True},
                                                     {'id': 2, 'from': 'a@b', 'important': 'yes'},
                                                     {'id': 3, 'from': 'a@b'}]})
        self.assertIs(rows[0]['important'], True)
        self.assertNotIn('important', rows[1])
        self.assertNotIn('important', rows[2])

    def test_sent_with_high_importance_says_when_gmails_copy_was_not_marked(self):
        import thunderbird_mailbox
        from email_delivery import delivery_note
        from pathlib import Path
        from email_draft import VoiceEmail
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        draft = VoiceEmail(Path(folder.name) / 'Email Drafts', title='Urgent', recipient='ann@example.com', subject='Urgent')
        draft.importance = 'high'
        sent = []

        class Bridge:
            def request(self, method, params, timeout=None, progress=None, every=None):
                if method == 'send':
                    sent.append(params)
                    return {'headerMessageId': 'abc@gmail.com', 'identityId': 'id1', 'sentImportant': self.marked}
                return {}
        bridge = Bridge()
        account = {'identity_id': 'id1', 'address': 'me@gmail.com'}
        with patch.object(thunderbird_mailbox, 'relay', return_value=bridge):
            bridge.marked = 'done'
            result = thunderbird_mailbox.submit(draft, account)
            self.assertEqual(result, 'abc@gmail.com')
            self.assertEqual(delivery_note(result), '')
            self.assertEqual(sent[-1]['priority'], 'high')
            bridge.marked = 'failed'
            self.assertIn('Gmail did not mark its copy in Sent Mail as important', delivery_note(thunderbird_mailbox.submit(draft, account)))
            bridge.marked = 'pending'
            self.assertEqual(delivery_note(thunderbird_mailbox.submit(draft, account)), '')


if __name__ == '__main__':
    unittest.main()

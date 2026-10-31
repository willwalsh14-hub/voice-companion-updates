import json
import tempfile
import unittest
from pathlib import Path
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from unittest.mock import patch
from types import SimpleNamespace

from email_delivery import DeliveryError, message_parts, submit, yahoo_submit
from document_editor import Paragraph


class Draft:
    recipient = 'friend@example.com'
    subject = 'Hello'
    cc = []
    response_context = {}
    paragraphs = [Paragraph(text='First line', bold=True), Paragraph(text='Second line')]


def yahoo_message(draft):
    """The message yahoo_submit hands to Yahoo's server, parsed as the receiver reads it."""
    sent = []
    class FakeSMTP:
        def __init__(self, host, port, context, timeout): pass
        def login(self, address, password): pass
        def send_message(self, message):
            sent.append(message); return {}
        def quit(self): pass
        def close(self): pass
    yahoo_submit(draft, 'me@yahoo.com', 'app-secret', FakeSMTP)
    return BytesParser(policy=policy.default).parsebytes(sent[0].as_bytes())


class DeliveryTests(unittest.TestCase):
    def sent_by_yahoo(self, draft):
        return yahoo_message(draft)

    def test_message_contains_only_expected_parts(self):
        message = self.sent_by_yahoo(Draft())
        self.assertEqual(message['To'], Draft.recipient)
        self.assertEqual(message['Subject'], Draft.subject)
        self.assertIn('First line\n\nSecond line', message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('<strong>First line</strong>', message.get_body(preferencelist=('html',)).get_content())
        self.assertNotIn('X-Unsent', message)

    def test_reply_all_preserves_thread_and_recipients(self):
        from email_draft import VoiceEmail
        from pathlib import Path
        draft = VoiceEmail(Path('/unused'), recipient='friend@example.com', subject='Re: Meeting', provider='yahoo')
        draft.cc = ['colleague@example.com']
        draft.paragraphs = [Paragraph(text='I agree.')]
        draft.response_context = {'action':'reply_all', 'source_id':'source',
                                  'message_id':'<original@example.com>',
                                  'references':'<earlier@example.com>'}
        message = self.sent_by_yahoo(draft)
        self.assertEqual(message['Cc'], 'colleague@example.com')
        self.assertEqual(message['In-Reply-To'], '<original@example.com>')
        self.assertEqual(message['References'], '<earlier@example.com> <original@example.com>')

    def test_forward_keeps_original_text_without_claiming_attachments(self):
        from email_draft import VoiceEmail
        from pathlib import Path
        draft = VoiceEmail(Path('/unused'), recipient='other@example.com', subject='Fwd: Meeting', provider='yahoo')
        draft.paragraphs = [Paragraph(text='Please read this.')]
        draft.response_context = {'action':'forward', 'source_id':'source',
                                  'original_body':'Original words.'}
        message = self.sent_by_yahoo(draft)
        self.assertIn('Original words.', message.get_body(preferencelist=('plain',)).get_content())
        self.assertNotIn('In-Reply-To', message)

    def test_removed_kinds_of_account_cannot_send(self):
        # Gmail and Microsoft work through Thunderbird now; nothing is sent another way.
        for provider in ('gmail', 'outlook', ''):
            with self.assertRaises(DeliveryError) as raised:
                submit(provider, Draft(), 'token')
            self.assertFalse(raised.exception.uncertain)
            self.assertIn('Thunderbird or Yahoo', str(raised.exception))

    def test_yahoo_uses_encrypted_smtp_and_app_password(self):
        class FakeSMTP:
            def __init__(self, host, port, context, timeout):
                self.host, self.port = host, port
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def login(self, address, password):
                self.credentials = (address, password)
            def send_message(self, message):
                self.message = message
                self_test.assertEqual(self.host, 'smtp.mail.yahoo.com')
                self_test.assertEqual(self.port, 465)
                self_test.assertEqual(self.credentials, ('me@yahoo.com', 'app-secret'))
                self_test.assertEqual(message['From'], 'me@yahoo.com')
                return {}
        self_test = self
        self.assertEqual(yahoo_submit(Draft(), 'me@yahoo.com', 'app-secret', FakeSMTP), 'accepted by Yahoo SMTP')

    def test_invalid_or_missing_data_cannot_send(self):
        d = Draft()
        d.recipient = 'two@example.com,other@example.com'
        with self.assertRaises(ValueError): message_parts(d)
        d.recipient = 'friend@example.com'
        d.subject = 'Hello\r\nBcc: other@example.com'
        with self.assertRaises(DeliveryError): message_parts(d)
        with self.assertRaises(DeliveryError): yahoo_submit(Draft(), 'me@yahoo.com', '')

    def test_voice_send_requires_review_and_exact_confirmation(self):
        import tempfile
        from pathlib import Path
        import companion
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder))
            draft.provider, draft.recipient, draft.subject = 'thunderbird', 'friend@example.com', 'Hello'
            draft.paragraphs = [Paragraph(text='First line'), Paragraph(text='Second line')]
            draft.save()
            with patch.object(companion, 'APP', Path(folder)), \
                 patch.object(companion, 'email_draft', draft), \
                 patch.object(companion.ProtectedStore, 'load', return_value={'address':'me@example.com'}), \
                 patch.object(companion, 'account_token', return_value=('me@example.com','token')), \
                 patch.object(companion, 'submit', return_value='provider-id') as send, \
                 patch.object(companion, 'speak') as speech:
                companion.pending_send = None

                companion.handle('confirm send email', 'email_draft')
                send.assert_not_called()
                companion.handle('send email', 'email_draft')
                self.assertIn('From Thunderbird, me@example.com', speech.call_args.args[0])
                self.assertIn('First line', speech.call_args.args[0])
                companion.handle('cancel send', 'email_draft')
                companion.handle('confirm send email', 'email_draft')
                send.assert_not_called()
                companion.handle('send email', 'email_draft')
                companion.handle('confirm send email', 'email_draft')
                send.assert_called_once()
                self.assertEqual(draft.last_accepted_hash, draft.send_hash())
                companion.handle('send email', 'email_draft')
                self.assertIn('already attempted', speech.call_args.args[0])
                send.assert_called_once()
                reopened = VoiceEmail.open_existing(Path(folder), draft.title)
                self.assertEqual(reopened.last_attempt_hash, draft.send_hash())
                companion.pending_send = None

    def test_natural_email_body_send_that_and_yes_still_require_review(self):
        import tempfile
        from pathlib import Path
        import companion
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(companion, 'APP', Path(folder)), \
             patch.object(companion, 'speak') as speech, \
             patch.object(companion.ProtectedStore, 'load', return_value={'address':'me@example.com'}), \
             patch.object(companion, 'account_token', return_value=('me@example.com','token')), \
             patch.object(companion, 'submit', return_value='accepted') as send:
            companion.pending_send = None
            mode = companion.handle('write an email', 'awake')
            draft = companion.email_draft
            draft.provider = 'thunderbird'
            companion.handle('email to friend at example dot com', mode,typed=True)
            companion.handle('subject is Hello', mode)
            companion.handle('I will see you on Tuesday.', mode)
            self.assertIn('I will see you on Tuesday.', draft.paragraphs[0].text)
            companion.handle('type literally send it', mode)
            self.assertIn('send it', draft.paragraphs[0].text)
            companion.handle('send it tomorrow', mode)
            self.assertIn('send it tomorrow', draft.paragraphs[0].text)
            companion.handle('send it', mode)
            self.assertIn('Review before sending', speech.call_args.args[0])
            companion.handle('cancel send', mode)
            companion.handle('yes', mode)
            send.assert_not_called()
            companion.handle('send that', mode)
            self.assertIn('Review before sending', speech.call_args.args[0])
            send.assert_not_called()
            companion.handle('yes', mode)
            send.assert_called_once()
            self.assertEqual(draft.last_accepted_hash, draft.send_hash())
            companion.pending_send = None

    def test_confirmed_send_returns_to_inbox_or_original_message(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        import companion
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='friend@example.com', subject='Hello', provider='thunderbird')
            draft.paragraphs = [Paragraph(text='One safe test.')]
            inbox = MagicMock()
            inbox.folder_name = 'Inbox'
            inbox.page_cursor = None
            inbox.list_messages.return_value = 'Inbox. Message 1.'
            with patch.object(companion, 'APP', Path(folder)), \
                 patch.object(companion, 'email_draft', draft), \
                 patch.object(companion, 'mail_session', None), \
                 patch.object(companion, 'MailSession', return_value=inbox), \
                 patch.object(companion.ProtectedStore, 'load', return_value={'address':'me@example.com'}), \
                 patch.object(companion, 'account_token', return_value=('me@example.com','token')), \
                 patch.object(companion, 'submit', return_value='accepted'), \
                 patch.object(companion, 'speak') as speech:
                companion.pending_send = None
                companion.handle('send it', 'email_draft')
                self.assertEqual(companion.handle('yes', 'email_draft'), 'mailbox')
                inbox.list_messages.assert_called_once_with(None)
                self.assertIn(('Email sent successfully.',), [c.args for c in speech.call_args_list])
                self.assertNotIn('Reference',speech.call_args.args[0])
                self.assertIn('Back to Inbox', speech.call_args.args[0])
            reply = VoiceEmail(Path(folder), title='Reply', recipient='friend@example.com',
                               subject='Re: Hello', provider='thunderbird')
            reply.paragraphs = [Paragraph(text='Reply text.')]
            reply.response_context = {'action':'reply','source_address':'me@example.com','source_id':'original'}
            original = MagicMock()
            original.view = 'message'
            original.body_text = 'Original body.'
            original.rows = [{'id':'original'}]
            original._next_body.return_value = 'Original body. End of message.'
            with patch.object(companion, 'APP', Path(folder)), \
                 patch.object(companion, 'email_draft', reply), \
                 patch.object(companion, 'mail_session', original), \
                 patch.object(companion.ProtectedStore, 'load', return_value={'address':'me@example.com'}), \
                 patch.object(companion, 'account_token', return_value=('me@example.com','token')), \
                 patch.object(companion, 'submit', return_value='accepted'), \
                 patch.object(companion, 'speak') as speech:
                companion.pending_send = None
                companion.handle('send that', 'email_draft')
                self.assertEqual(companion.handle('yes', 'email_draft'), 'mailbox')
                self.assertIn('original message', speech.call_args.args[0])
                original.reading.set_text.assert_called_once_with('Original body.', reset=True)

    def test_account_record_round_trip_and_disconnect(self):
        import sys
        import tempfile
        from pathlib import Path
        from mail_accounts import ProtectedStore, AccountError
        fake_dpapi = SimpleNamespace(
            CryptProtectData=lambda data, *args: b'protected:' + data,
            CryptUnprotectData=lambda data, *args: ('Voice Companion', data[len(b'protected:'):]))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'win32crypt': fake_dpapi}):
            store = ProtectedStore(folder)
            store.save('yahoo', {'address': 'me@example.com', 'app_password': 'secret'})
            self.assertTrue(store._account_path('yahoo', 'me@example.com').read_bytes().startswith(b'protected:'))
            self.assertEqual(store.load('yahoo')['address'], 'me@example.com')
            store.save('yahoo', {'address': 'other@example.com', 'app_password': 'second'})
            self.assertEqual(len(store.accounts()), 2)
            self.assertEqual(store.load('yahoo')['address'], 'other@example.com')
            store.select('yahoo', 'me@example.com')
            self.assertEqual(store.load('yahoo')['address'], 'me@example.com')
            store.disconnect('yahoo')
            self.assertNotIn(('yahoo', 'me@example.com'), store.accounts())
            store.select('yahoo', 'other@example.com')
            self.assertEqual(store.load('yahoo')['address'], 'other@example.com')
            # A removed kind of account cannot be saved again.
            with self.assertRaises(AccountError):
                store.save('gmail', {'address': 'me@gmail.com'})
            self.assertFalse(list(Path(folder).glob('gmail*')))

    def test_uncertain_send_remains_blocked_after_restart(self):
        import tempfile
        from pathlib import Path
        import companion
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='friend@example.com', subject='Hello', provider='yahoo')
            draft.paragraphs = [Paragraph(text='One message')]
            draft.save()
            with patch.object(companion, 'APP', Path(folder)), \
                 patch.object(companion, 'email_draft', draft), \
                 patch.object(companion.ProtectedStore, 'load', return_value={'address':'me@example.com'}), \
                 patch.object(companion, 'account_token', return_value=('me@example.com','token')), \
                 patch.object(companion, 'submit', side_effect=DeliveryError('timeout')) as send, \
                 patch.object(companion, 'speak') as speech:
                companion.pending_send = None
                companion.handle('send email', 'email_draft')
                companion.handle('confirm send email', 'email_draft')
                send.assert_called_once()
                companion.email_draft = VoiceEmail.open_existing(Path(folder), draft.title)
                companion.handle('send email', 'email_draft')
                self.assertIn('already attempted', speech.call_args.args[0])
                send.assert_called_once()
                companion.pending_send = None


if __name__ == '__main__': unittest.main()


class ImportanceAndReceiptTests(unittest.TestCase):
    """Will, October 30, 2026: set importance and ask for a read receipt while writing."""
    def draft(self, folder):
        from email_draft import VoiceEmail
        draft = VoiceEmail(Path(folder), recipient='sam@example.com', subject='Lunch', provider='yahoo')
        draft.paragraphs = [Paragraph('See you at noon.')]
        return draft

    def test_commands_change_and_say_the_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = self.draft(folder)
            self.assertEqual(draft.process('Importance'), 'Normal importance. No read receipt.')
            self.assertEqual(draft.process('change importance'), 'High importance.')
            self.assertEqual(draft.process('change importance'), 'Low importance.')
            self.assertEqual(draft.process('change importance'), 'Normal importance.')
            self.assertEqual(draft.process('High importance.'), 'High importance.')
            self.assertEqual(draft.process('read receipt on or off'),
                             'Read receipt on. The person is asked to tell you when they open it.')
            self.assertEqual(draft.process('Read receipt.'), 'Read receipt on. The person is asked to tell you when they open it.')
            # A sentence that only mentions importance is part of the message.
            draft.compose_step = 'body'
            draft.process('This is of high importance.')
            self.assertIn('This is of high importance.', ' '.join(p.text for p in draft.paragraphs))
            self.assertEqual(draft.importance, 'high')
            # In the To and Subject boxes the commands still work, never becoming an address.
            draft.compose_step = 'recipient'
            self.assertEqual(draft.process('Low importance'), 'Low importance.')
            self.assertEqual(draft.recipient, 'sam@example.com')
            self.assertEqual(draft.process('No read receipt.'), 'No read receipt.')

    def test_subject_box_keeps_importance_words_as_the_subject(self):
        """Sweep, October 31, 2026: "High priority" said in the Subject box is the subject."""
        with tempfile.TemporaryDirectory() as folder:
            draft = self.draft(folder)
            draft.compose_step = 'subject'
            answer = draft.process('High priority.')
            self.assertEqual(draft.subject, 'High priority')
            self.assertEqual(draft.importance, '')
            self.assertIn('Subject recorded as High priority', answer)
            # The keys still work in the Subject box.
            draft.compose_step = 'subject'
            self.assertEqual(draft.process('change importance'), 'High importance.')
            self.assertEqual(draft.subject, 'High priority')

    def test_saved_and_reopened_with_the_draft(self):
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = self.draft(folder)
            before = draft.send_hash()
            draft.process('high importance'); draft.process('request a read receipt')
            self.assertNotEqual(draft.send_hash(), before, 'a change after the review needs a new review')
            again = VoiceEmail.open_existing(Path(folder), draft.title)
            self.assertEqual((again.importance, again.read_receipt), ('high', True))
            # Damaged values open as normal importance and no receipt.
            state = json.loads(draft.state_path.read_text(encoding='utf-8'))
            state.update(importance='urgent!!', read_receipt='yes')
            draft.state_path.write_text(json.dumps(state), encoding='utf-8')
            damaged = VoiceEmail.open_existing(Path(folder), draft.title)
            self.assertEqual((damaged.importance, damaged.read_receipt), ('', False))

    def test_old_drafts_keep_their_send_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = self.draft(folder)
            content = [draft.provider, draft.recipient, draft.subject, draft.cc, draft.bcc, draft.response_context,
                       [__import__('dataclasses').asdict(p) for p in draft.paragraphs]]
            import hashlib
            self.assertEqual(draft.send_hash(), hashlib.sha256(json.dumps(content, ensure_ascii=False).encode('utf-8')).hexdigest())

    def test_yahoo_message_carries_importance_and_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = self.draft(folder)
            plain = yahoo_message(draft)
            self.assertIsNone(plain['X-Priority']); self.assertIsNone(plain['Disposition-Notification-To'])
            draft.importance = 'high'; draft.read_receipt = True
            message = yahoo_message(draft)
            self.assertEqual((message['X-Priority'], message['Importance']), ('2 (High)', 'High'))
            self.assertEqual(message['Disposition-Notification-To'], 'me@yahoo.com')
            draft.importance = 'low'
            self.assertEqual(yahoo_message(draft)['X-Priority'], '4 (Low)')

import base64
import json
import unittest
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from urllib.error import URLError
from unittest.mock import patch
from types import SimpleNamespace

from email_delivery import DeliveryError, gmail_request, microsoft_request, submit, yahoo_submit
from document_editor import Paragraph


class Draft:
    recipient = 'friend@example.com'
    subject = 'Hello'
    cc = []
    response_context = {}
    paragraphs = [Paragraph(text='First line', bold=True), Paragraph(text='Second line')]


class Response:
    def __init__(self, status, data):
        self.status, self.data = status, data

    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, length): return self.data


class DeliveryTests(unittest.TestCase):
    def test_gmail_request_contains_only_expected_message(self):
        req = gmail_request(Draft(), 'test-token')
        self.assertEqual(req.full_url, 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')
        raw = json.loads(req.data)['raw']
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(message['To'], Draft.recipient)
        self.assertEqual(message['Subject'], Draft.subject)
        self.assertIn('First line\n\nSecond line', message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('<strong>First line</strong>', message.get_body(preferencelist=('html',)).get_content())
        self.assertNotIn('X-Unsent', message)

    def test_microsoft_request_saves_sent_item(self):
        payload = json.loads(microsoft_request(Draft(), 'test-token').data)
        self.assertEqual(payload['message']['toRecipients'][0]['emailAddress']['address'], Draft.recipient)
        self.assertEqual(payload['message']['body']['contentType'], 'HTML')
        self.assertIn('<strong>First line</strong>', payload['message']['body']['content'])
        self.assertTrue(payload['saveToSentItems'])

    def test_reply_all_preserves_thread_and_recipients(self):
        from email_draft import VoiceEmail
        from pathlib import Path
        draft = VoiceEmail(Path('/unused'), recipient='friend@example.com', subject='Re: Meeting', provider='gmail')
        draft.cc = ['colleague@example.com']
        draft.paragraphs = [Paragraph(text='I agree.')]
        draft.response_context = {'action':'reply_all', 'source_id':'source',
                                  'thread_id':'thread', 'message_id':'<original@example.com>',
                                  'references':'<earlier@example.com>'}
        payload = json.loads(gmail_request(draft, 'token').data)
        self.assertEqual(payload['threadId'], 'thread')
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload['raw']))
        self.assertEqual(message['Cc'], 'colleague@example.com')
        self.assertEqual(message['In-Reply-To'], '<original@example.com>')
        draft.provider = 'outlook'
        graph = microsoft_request(draft, 'token')
        self.assertTrue(graph.full_url.endswith('/messages/source/replyAll'))
        self.assertNotIn('comment',json.loads(graph.data))
        self.assertIn('I agree.',json.loads(graph.data)['message']['body']['content'])

    def test_forward_keeps_original_text_without_claiming_attachments(self):
        from email_draft import VoiceEmail
        from pathlib import Path
        draft = VoiceEmail(Path('/unused'), recipient='other@example.com', subject='Fwd: Meeting', provider='gmail')
        draft.paragraphs = [Paragraph(text='Please read this.')]
        draft.response_context = {'action':'forward', 'source_id':'source',
                                  'original_body':'Original words.'}
        payload = json.loads(gmail_request(draft, 'token').data)
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload['raw']))
        self.assertIn('Original words.', message.get_body(preferencelist=('plain',)).get_content())
        self.assertNotIn('threadId', payload)
        draft.provider = 'outlook'
        graph = microsoft_request(draft, 'token')
        self.assertTrue(graph.full_url.endswith('/messages/source/forward'))
        self.assertEqual(json.loads(graph.data)['toRecipients'][0]['emailAddress']['address'], 'other@example.com')

    def test_acceptance_and_uncertain_failure(self):
        self.assertEqual(submit('gmail', Draft(), 'token', lambda req, timeout: Response(200, b'{"id":"abc"}')), 'abc')
        self.assertEqual(submit('outlook', Draft(), 'token', lambda req, timeout: Response(202, b'')), 'accepted by Microsoft Graph')
        def fail(req, timeout): raise URLError('timeout')
        with self.assertRaisesRegex(DeliveryError, 'uncertain'):
            submit('gmail', Draft(), 'token', fail)

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
        with self.assertRaises(ValueError): gmail_request(d, 'token')
        d.recipient = 'friend@example.com'
        d.subject = 'Hello\r\nBcc: other@example.com'
        with self.assertRaises(DeliveryError): microsoft_request(d, 'token')
        with self.assertRaises(DeliveryError): gmail_request(Draft(), '')

    def test_voice_send_requires_review_and_exact_confirmation(self):
        import tempfile
        from pathlib import Path
        import companion
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder))
            draft.provider, draft.recipient, draft.subject = 'gmail', 'friend@example.com', 'Hello'
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
                self.assertIn('From me@example.com', speech.call_args.args[0])
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
            draft.provider = 'gmail'
            companion.handle('email to friend at example dot com', mode)
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
            draft = VoiceEmail(Path(folder), recipient='friend@example.com', subject='Hello', provider='gmail')
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
                               subject='Re: Hello', provider='gmail')
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
        from mail_accounts import ProtectedStore, AccountError
        fake_dpapi = SimpleNamespace(
            CryptProtectData=lambda data, *args: b'protected:' + data,
            CryptUnprotectData=lambda data, *args: ('Voice Companion', data[len(b'protected:'):]))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'win32crypt': fake_dpapi}):
            store = ProtectedStore(folder)
            store.save('gmail', {'address': 'me@example.com', 'credentials': 'secret'})
            self.assertTrue(store._account_path('gmail', 'me@example.com').read_bytes().startswith(b'protected:'))
            self.assertEqual(store.load('gmail')['address'], 'me@example.com')
            store.save('gmail', {'address': 'other@example.com', 'credentials': 'second'})
            self.assertEqual(len(store.accounts()), 2)
            self.assertEqual(store.load('gmail')['address'], 'other@example.com')
            store.select('gmail', 'me@example.com')
            self.assertEqual(store.load('gmail')['address'], 'me@example.com')
            store.disconnect('gmail')
            self.assertNotIn(('gmail', 'me@example.com'), store.accounts())
            store.select('gmail', 'other@example.com')
            self.assertEqual(store.load('gmail')['address'], 'other@example.com')

    def test_uncertain_send_remains_blocked_after_restart(self):
        import tempfile
        from pathlib import Path
        import companion
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='friend@example.com', subject='Hello', provider='outlook')
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

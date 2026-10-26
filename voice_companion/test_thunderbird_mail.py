"""Thunderbird mail connection, end to end.

The real add-on (background.js) runs in Node against a simulated Thunderbird,
speaks native messaging to the real host code (thunderbird_bridge.native_stream),
which connects to Companion's real relay. Companion then reads, organizes and
sends mail through ThunderbirdMailbox exactly as the app does.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import mail_accounts
import regular_browser
import thunderbird_bridge
import thunderbird_mailbox
from browser_bridge_host import read_message
from document_editor import Paragraph
from email_delivery import DeliveryError, submit
from email_draft import VoiceEmail
from mail_voice import MailSession
from mailbox_access import MailboxError
from thunderbird_mailbox import ThunderbirdMailbox

HERE = Path(__file__).parent
ACCOUNT = {'address': 'me@gmail.com', 'account_id': 'account1', 'identity_id': 'id1'}


def plain_store():
    """ProtectedStore without Windows DPAPI: plain JSON, for tests only."""
    return [mock.patch.object(mail_accounts.ProtectedStore, '_seal', staticmethod(lambda data: json.dumps(data).encode())),
            mock.patch.object(mail_accounts.ProtectedStore, '_unseal', staticmethod(lambda path: json.loads(path.read_bytes())))]


def simulated(**env):
    """How the simulated Thunderbird starts for one test (fake-thunderbird.cjs reads these)."""
    def mark(test):
        test.thunderbird_env = {name: str(value) for name, value in env.items()}
        return test
    return mark


@unittest.skipUnless(shutil.which('node'), 'Node.js runs the simulated Thunderbird')
class ThunderbirdEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        if os.name == 'nt':
            endpoint, family = None, 'AF_PIPE'
        else:
            endpoint, family = str(self.folder / 'mail.sock'), 'AF_UNIX'
        self.relay = thunderbird_bridge.MailRelay(self.folder, endpoint, family)
        self.addCleanup(self.relay.close)
        for patcher in [mock.patch.object(thunderbird_bridge, '_RELAY', self.relay),
                        mock.patch.object(regular_browser, 'data_folder', lambda: self.folder)] + plain_store():
            patcher.start(); self.addCleanup(patcher.stop)
        env = dict(os.environ, **getattr(getattr(self, self._testMethodName), 'thunderbird_env', {}))
        self.thunderbird = subprocess.Popen(['node', str(HERE / 'thunderbird_mail_extension' / 'fake-thunderbird.cjs')],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        first = read_message(self.thunderbird.stdout)
        self.assertEqual((first['protocol'], first['command']), (3, 'mail'))
        self.host = threading.Thread(target=thunderbird_bridge.native_stream,
                                     args=(first, self.thunderbird.stdout, self.thunderbird.stdin, endpoint, family), daemon=True)
        self.host.start()
        self.assertTrue(self.relay.connected.wait(10), 'Thunderbird did not connect')
        manifest = json.loads((HERE / 'thunderbird_mail_extension' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(self.relay.version, manifest['version'])

    def finish(self):
        """Close Thunderbird's input; it reports what it sent and moved."""
        self.thunderbird.stdin.close()
        _, report = self.thunderbird.communicate(timeout=20)
        return json.loads(report)

    def tearDown(self):
        if self.thunderbird.poll() is None:
            self.thunderbird.kill(); self.thunderbird.communicate()

    def test_connect_list_read_reply_move_and_delete(self):
        store = mail_accounts.ProtectedStore(self.folder)
        self.assertEqual(mail_accounts.connect('thunderbird', self.folder), 'me@gmail.com')
        self.assertEqual(store.selected(), ('thunderbird', 'me@gmail.com'))
        address, account = mail_accounts.account_token('thunderbird', self.folder)
        self.assertEqual((address, account['account_id'], account['identity_id']), ('me@gmail.com', 'account1', 'id1'))

        mailbox = ThunderbirdMailbox(address, account)
        self.assertEqual([name for name, _ in mailbox.folders()], ['Inbox', 'Sent Mail', 'Receipts', 'Trash'])
        rows, cursor = mailbox.list_messages('inbox')
        self.assertEqual([row['subject'] for row in rows[:2]], ['Message 25', 'Message 24'])
        self.assertEqual((rows[0]['from'], rows[0]['sender_name'], rows[0]['unread']), ('sender25@example.com', 'Sender 25', True))
        self.assertEqual(rows[0]['date'], 'October 25, 2026')
        self.assertEqual(cursor, '10')
        more, cursor = mailbox.list_messages('inbox', cursor, 20)
        self.assertEqual((len(more), cursor), (15, None))

        body = mailbox.read_message(rows[0]['id'])
        self.assertIn('Latest news', str(body))
        self.assertNotIn('<b>', str(body))
        context = mailbox.response_context(rows[0]['id'], 'reply_all', address)
        self.assertEqual((context['recipient'], context['cc'], context['subject']),
                         ('replies@example.com', ['other@example.com'], 'Re: Message 25'))

        receipts = dict(mailbox.folders())['Receipts']
        mailbox.move([rows[1]['id']], 'inbox', receipts)
        self.assertEqual(mailbox.folder_info(receipts)['messagesTotal'], 1)
        mailbox.trash([rows[2]['id']], 'inbox')
        with self.assertRaises(MailboxError):
            mailbox.read_message('not a number')
        report = self.finish()
        self.assertEqual(report['moved'], [{'ids': [24], 'folderId': 'account1://Receipts'}])
        self.assertEqual(report['deleted'], [{'ids': [23], 'skipTrash': False}])

    def test_inbox_screen_uses_thunderbird(self):
        mail_accounts.connect('thunderbird', self.folder)
        session = MailSession('thunderbird', self.folder)
        spoken = session.list_messages()
        self.assertIn('Message 25', spoken)
        self.assertIn('Sender 25', spoken)

    @simulated(FAKE_THUNDERBIRD_SERVER_MAIL=1)
    def test_list_shows_mail_that_just_reached_the_server(self):
        # Will, October 23, 2026: Thunderbird with Gmail did not show the latest messages, while
        # Voice Companion's own Gmail did. Thunderbird had not downloaded them yet: Voice Companion
        # starts it in the background and lists at once, before Thunderbird checks the server.
        mail_accounts.connect('thunderbird', self.folder)
        address, account = mail_accounts.account_token('thunderbird', self.folder)
        mailbox = ThunderbirdMailbox(address, account)
        rows, cursor = mailbox.list_messages('inbox')
        self.assertEqual([row['subject'] for row in rows[:2]], ['Message 26', 'Message 25'])
        self.assertEqual(rows[0]['date'], 'October 26, 2026')
        self.assertEqual(mailbox.notice, '')
        more, _ = mailbox.list_messages('inbox', cursor)
        self.assertEqual(more[0]['subject'], 'Message 16')   # later pages follow on, without a gap
        session = MailSession('thunderbird', self.folder)
        spoken = session.list_messages()
        self.assertIn('Message 26', spoken)
        self.assertNotIn('Thunderbird could not check', spoken)

    def test_end_goes_to_the_oldest_page_in_one_request(self):
        # Will, 0.2.161: End read every page in turn; a large work mailbox never finished.
        mail_accounts.connect('thunderbird', self.folder)
        session = MailSession('thunderbird', self.folder)
        said = []; session.progress = said.append
        session.list_messages()
        requests = []
        original = self.relay.request
        def counted(method, params=None, timeout=60):
            requests.append(method); return original(method, params, timeout)
        with mock.patch.object(self.relay, 'request', counted):
            spoken = session.list_boundary(True)
        self.assertEqual(requests.count('list'), 1)
        self.assertIn('Message 1.', spoken); self.assertIn('5 of 5', spoken)
        self.assertEqual(session.page_cursor, '20')
        self.assertEqual(said[-1], 'Going to the end of Inbox. Please wait.')
        # Page Up from the end goes back one page at a time, then to the newest.
        self.assertIn('Message 15', session.process('previous messages'))
        self.assertIn('Message 25', session.process('previous messages'))

    @simulated(FAKE_THUNDERBIRD_SERVER_MAIL=1, FAKE_THUNDERBIRD_MAIL_CHECK='fail')
    def test_failed_mail_check_is_said_not_silent(self):
        mail_accounts.connect('thunderbird', self.folder)
        spoken = MailSession('thunderbird', self.folder).list_messages()
        self.assertIn('Message 25', spoken)      # the mail Thunderbird has is still listed
        self.assertIn('Thunderbird could not reach your mail server, so the newest messages may be missing.', spoken)

    @simulated(FAKE_THUNDERBIRD_MAIL_CHECK='offline')
    def test_offline_thunderbird_is_said(self):
        mail_accounts.connect('thunderbird', self.folder)
        spoken = MailSession('thunderbird', self.folder).list_messages()
        self.assertIn('Thunderbird is set to work offline', spoken)

    @simulated(FAKE_THUNDERBIRD_MAIL_CHECK='hang', FAKE_THUNDERBIRD_CHECK_MS=100)
    def test_slow_mail_check_still_lists(self):
        mail_accounts.connect('thunderbird', self.folder)
        spoken = MailSession('thunderbird', self.folder).list_messages()
        self.assertIn('Message 25', spoken)
        self.assertIn('Thunderbird is still getting your mail, so the newest messages may not be here yet.', spoken)

    def test_send_new_and_reply(self):
        mail_accounts.connect('thunderbird', self.folder)
        address, account = mail_accounts.account_token('thunderbird', self.folder)
        draft = VoiceEmail(self.folder / 'Email Drafts', recipient='sam@example.com', subject='Lunch', provider='thunderbird',
                           cc=['amy@example.com'], bcc=['boss@example.com'])
        draft.paragraphs = [Paragraph('See you at noon.')]
        self.assertTrue(submit('thunderbird', draft, account, sender=address).endswith('@thunderbird.test'))

        reply = VoiceEmail(self.folder / 'Email Drafts', recipient='replies@example.com', subject='Re: Message 25',
                           provider='thunderbird', response_context={'action': 'reply_all', 'source_id': 25})
        reply.paragraphs = [Paragraph('Thanks!')]
        submit('thunderbird', reply, account, sender=address)
        sent = self.finish()['sent']
        self.assertEqual(sent[0]['kind'], 'new')
        self.assertEqual((sent[0]['identityId'], sent[0]['to'], sent[0]['cc'], sent[0]['bcc'], sent[0]['subject']),
                         ('id1', ['sam@example.com'], ['amy@example.com'], ['boss@example.com'], 'Lunch'))
        self.assertIn('See you at noon.', sent[0]['body'])
        self.assertEqual((sent[1]['kind'], sent[1]['replyType'], sent[1]['messageId']), ('reply', 'replyToAll', 25))
        self.assertIn('Thanks!', sent[1]['body'])
        self.assertIn('Original message', sent[1]['body'])
        self.assertNotIn('Forwarded message', sent[1]['body'])


class ThunderbirdOfflineTests(unittest.TestCase):
    def test_thunderbird_closed_gives_a_plain_message(self):
        with mock.patch.object(thunderbird_bridge, '_RELAY', None):
            # Never "open Thunderbird yourself": the app starts it (STARTER); without that, say how to check.
            with self.assertRaisesRegex(MailboxError, 'Say add account and choose Thunderbird'):
                ThunderbirdMailbox('me@gmail.com', ACCOUNT).folders()
            draft = VoiceEmail(Path(tempfile.mkdtemp()), recipient='sam@example.com', subject='Hi')
            draft.paragraphs = [Paragraph('Hello')]
            with self.assertRaises(DeliveryError) as raised:
                submit('thunderbird', draft, ACCOUNT, sender='me@gmail.com')
            self.assertFalse(raised.exception.uncertain)  # nothing reached Thunderbird

    def test_host_manifest_only_allows_the_mail_add_on(self):
        manifest = thunderbird_bridge.host_manifest('C:/Voice Companion/VoiceCompanion-BrowserHost.exe')
        self.assertEqual(manifest['name'], 'com.voicecompanion.mail')
        self.assertEqual(manifest['allowed_extensions'], ['voice-companion-mail@willwalsh14-hub'])
        add_on = json.loads((HERE / 'thunderbird_mail_extension' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(add_on['browser_specific_settings']['gecko']['id'], manifest['allowed_extensions'][0])
        self.assertIn('nativeMessaging', add_on['permissions'])

    def test_add_on_package_contains_only_the_add_on(self):
        import zipfile
        from build_thunderbird_addon import build
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(build(Path(folder) / 'mail.xpi')) as archive:
                self.assertEqual(sorted(archive.namelist()), ['background.js', 'experiment/implementation.js',
                                                              'experiment/schema.json', 'manifest.json'])

    def test_send_review_accepts_a_thunderbird_draft(self):
        # The send step listed only Gmail, Outlook and Yahoo, so a Thunderbird
        # draft heard "Choose a connected email account first" and never sent.
        import companion
        folder = Path(tempfile.mkdtemp())
        draft = VoiceEmail(folder / 'Email Drafts', recipient='sam@example.com', subject='Lunch', provider='thunderbird')
        draft.paragraphs = [Paragraph('See you at noon.')]
        spoken = []
        with mock.patch.object(companion, 'email_draft', draft), mock.patch.object(companion, 'pending_send', None), \
             mock.patch.object(companion, 'APP', folder), mock.patch.object(companion, 'TEXT_MODE', True), \
             mock.patch.object(companion, 'APP_WINDOW', None), mock.patch.object(companion, 'speak', spoken.append), \
             mock.patch.object(mail_accounts.ProtectedStore, 'load', lambda self, provider: {'address': 'me@gmail.com'}):
            companion.handle('send email', 'email_draft')
            self.assertEqual(companion.pending_send[1:], ('thunderbird', 'me@gmail.com'))
        self.assertFalse(any('Choose a connected email account' in line for line in spoken), spoken)

    def test_account_type_menu_offers_thunderbird_then_yahoo_only(self):
        import companion
        self.assertEqual([kind for _, kind in companion.ACCOUNT_TYPES], ['thunderbird', 'yahoo'])
        # The Thunderbird item says it is the way for Gmail and Microsoft mail.
        label = companion.ACCOUNT_TYPES[0][0]
        for word in ('Thunderbird', 'Gmail', 'Outlook', 'Microsoft 365'):
            self.assertIn(word, label)


if __name__ == '__main__':
    unittest.main()


class EndOfLongListTests(unittest.TestCase):
    """Services without a one-step End go page by page, but never without end."""
    def test_page_by_page_end_stops_and_says_what_to_do(self):
        class EndlessMailbox:
            calls = 0
            def list_messages(self, folder_id, cursor=None, limit=10):
                EndlessMailbox.calls += 1
                start = int(cursor or 0)
                rows = [{'id': str(start + n), 'from': 'a@example.com', 'sender_name': 'A', 'subject': 'S' + str(start + n),
                         'date': 'October 1, 2026', 'size': 1, 'unread': False} for n in range(limit)]
                return rows, str(start + limit)
        with tempfile.TemporaryDirectory() as folder:
            session = MailSession('yahoo', Path(folder))
            said = []; session.progress = said.append
            with mock.patch.object(MailSession, 'client', lambda self: EndlessMailbox()):
                session.list_messages()
                said.clear()
                spoken = session.list_boundary(True)
        self.assertIn('too long to go to the end here', spoken)
        self.assertIn('Page Down', spoken)
        self.assertLessEqual(EndlessMailbox.calls, MailSession.MAX_END_PAGES + 2)
        self.assertEqual(said, ['Going to the end of Inbox. Please wait.'])


class SendAndCloseTests(unittest.TestCase):
    """Will, 0.2.162: an Office 365 send through Thunderbird took over two minutes with no
    word, and Thunderbird stayed open after Voice Companion closed."""
    def relay(self, answer_after=None):
        bridge = thunderbird_bridge.MailRelay.__new__(thunderbird_bridge.MailRelay)
        bridge.connected = threading.Event(); bridge.connected.set()
        bridge.pending = {}; bridge.ids = iter(range(1, 100)); bridge.send_lock = threading.Lock()
        sent = []
        class Pipe:
            def send_bytes(self, data):
                sent.append(data)
                if answer_after is not None:
                    def answer():
                        for waiter in bridge.pending.values():
                            waiter['answer'] = {'result': {'headerMessageId': 'm1@example.com'}}; waiter['event'].set()
                    threading.Timer(answer_after, answer).start()
        bridge.pipe = Pipe()
        return bridge, sent

    def draft(self):
        return mock.Mock(recipient='boss@work.example', subject='testing', cc=[], bcc=[], response_context={})

    def test_a_slow_send_says_it_is_still_sending_then_finishes(self):
        import user_stop
        user_stop.begin_command()
        bridge, _ = self.relay(answer_after=0.5)
        said = []
        result = bridge.request('send', {}, timeout=5, progress=lambda: said.append(thunderbird_mailbox.STILL_SENDING), every=0.2)
        self.assertEqual(result, {'headerMessageId': 'm1@example.com'})
        self.assertGreaterEqual(said.count(thunderbird_mailbox.STILL_SENDING), 1)
        # submit() passes Companion's status speech to the wait.
        calls = []
        with mock.patch.object(thunderbird_bridge, '_RELAY', bridge), \
             mock.patch.object(thunderbird_mailbox, 'PROGRESS', said.append), \
             mock.patch.object(bridge, 'request', lambda method, params=None, timeout=60, progress=None, every=15:
                               calls.append((method, timeout, progress)) or {'headerMessageId': 'm2'}), \
             mock.patch('email_delivery.message_parts', lambda draft: ('boss@work.example', 'testing', 'Hi')), \
             mock.patch('email_formatting.formatted_body', lambda draft: '<p>Hi</p>'):
            self.assertEqual(thunderbird_mailbox.submit(self.draft(), {'identity_id': 'id1'}), 'm2')
        method, timeout, progress = next(call for call in calls if call[0] == 'send')
        self.assertEqual((method, timeout), ('send', 120))
        said.clear()
        with mock.patch('time.monotonic', lambda: 10 ** 9): progress()   # 15 seconds have passed
        self.assertEqual(said, ['Still sending through Thunderbird. Press Escape to stop waiting.'])

    def test_escape_during_a_send_stops_waiting_and_warns_about_a_duplicate(self):
        import user_stop
        from email_delivery import DeliveryError
        user_stop.begin_command(); self.addCleanup(user_stop.begin_command)
        bridge, _ = self.relay(answer_after=None)
        threading.Timer(0.2, user_stop.request).start()
        with mock.patch.object(thunderbird_bridge, '_RELAY', bridge), \
             mock.patch('email_delivery.message_parts', lambda draft: ('boss@work.example', 'testing', 'Hi')), \
             mock.patch('email_formatting.formatted_body', lambda draft: '<p>Hi</p>'):
            with self.assertRaises(DeliveryError) as raised:
                thunderbird_mailbox.submit(self.draft(), {'identity_id': 'id1'})
        self.assertIn('Thunderbird may still send this message', str(raised.exception))
        self.assertIn('Check the Sent folder', str(raised.exception))
        self.assertTrue(raised.exception.uncertain)

    def test_thunderbird_started_by_companion_closes_with_it(self):
        import companion
        relay = mock.Mock(); relay.connected = threading.Event(); relay.connected.set()
        relay.request.return_value = {'quit': True}
        with mock.patch.object(companion, 'THUNDERBIRD_STARTED', False):
            self.assertIsNone(companion.close_started_thunderbird(lambda: relay))
            relay.request.assert_not_called()   # the user opened Thunderbird: it stays open
        registered = []
        with mock.patch.object(companion, 'THUNDERBIRD_STARTED', False), mock.patch('atexit.register', registered.append), \
             mock.patch.object(companion, 'APP_WINDOW', None):
            companion.quiet_thunderbird(relay); companion.quiet_thunderbird(relay)
            self.assertEqual(registered, [companion.close_started_thunderbird])
            self.assertEqual(companion.close_started_thunderbird(lambda: relay), {'quit': True})
        relay.request.assert_called_with('quit', {}, timeout=5)


class SignInWhileSendingTests(unittest.TestCase):
    """Will, 0.2.162: Office 365 asked Thunderbird for a sign-in to send. Thunderbird's page was
    out of reach, and the send timed out ("connection to smtp.office365.com timed out")."""
    class Bridge:
        def __init__(self, sign_in_after=1):
            self.calls = []; self.polls = 0; self.sign_in_after = sign_in_after
            self.connected = threading.Event(); self.connected.set()
        def request(self, method, params=None, timeout=60, progress=None, every=15):
            self.calls.append((method, params))
            if method == 'takeSignIn':
                self.polls += 1
                return {'url': 'https://login.microsoftonline.com/x', 'where': 'browser'} if self.polls > self.sign_in_after else {'url': '', 'where': ''}
            if method == 'send':
                for _ in range(10): progress()
                return {'headerMessageId': 'sent@x'}
            return {'capturing': bool(params and params.get('seconds'))}

    def submit(self, bridge):
        draft = mock.Mock(recipient='boss@work.example', subject='testing', cc=[], bcc=[], response_context={})
        with mock.patch.object(thunderbird_mailbox, 'relay', lambda: bridge), mock.patch.object(thunderbird_mailbox, 'PROGRESS', None), \
             mock.patch('email_delivery.message_parts', lambda draft: ('boss@work.example', 'testing', 'Hi')), \
             mock.patch('email_formatting.formatted_body', lambda draft: '<p>Hi</p>'):
            return thunderbird_mailbox.submit(draft, {'identity_id': 'id1'})

    def test_a_sign_in_page_during_a_send_is_handed_to_companion(self):
        from email_delivery import DeliveryError
        bridge = self.Bridge(sign_in_after=1)
        with self.assertRaises(DeliveryError) as raised:
            self.submit(bridge)
        self.assertEqual(raised.exception.sign_in_url, 'https://login.microsoftonline.com/x')
        self.assertIn('Microsoft wants you to sign in', str(raised.exception))
        self.assertTrue(raised.exception.uncertain)   # Thunderbird may send once signed in
        methods = [m for m, _ in bridge.calls]
        self.assertEqual(bridge.calls[0], ('captureSignIn', {'seconds': thunderbird_mailbox.SIGN_IN_SECONDS}))
        self.assertEqual(bridge.calls[-1], ('captureSignIn', {'seconds': 0}))
        self.assertIn('send', methods)

    def test_a_send_with_no_sign_in_finishes_and_stops_listening(self):
        bridge = self.Bridge(sign_in_after=99)
        self.assertEqual(self.submit(bridge), 'sent@x')
        self.assertEqual(bridge.calls[-1], ('captureSignIn', {'seconds': 0}))


class RememberWorkingIdentityTests(unittest.TestCase):
    def test_a_send_from_another_identity_is_remembered_with_the_address(self):
        calls, remembered = [], []
        class Bridge:
            def request(self, method, params=None, timeout=60, progress=None, every=15):
                calls.append((method, params))
                if method == 'send':
                    return {'headerMessageId': 'sent@x', 'identityId': 'id7', 'accountId': 'account7', 'tried': 2}
                return {'url': ''} if method == 'takeSignIn' else {'capturing': True}
        account = {'address': 'will@work.example', 'account_id': 'account5', 'identity_id': 'id5'}
        draft = mock.Mock(recipient='boss@work.example', subject='testing', cc=[], bcc=[], response_context={})
        with mock.patch.object(thunderbird_mailbox, 'relay', lambda: Bridge()), mock.patch.object(thunderbird_mailbox, 'PROGRESS', None), \
             mock.patch.object(thunderbird_mailbox, 'REMEMBER_IDENTITY', lambda *args: remembered.append(args)), \
             mock.patch('email_delivery.message_parts', lambda draft: ('boss@work.example', 'testing', 'Hi')), \
             mock.patch('email_formatting.formatted_body', lambda draft: '<p>Hi</p>'):
            self.assertEqual(thunderbird_mailbox.submit(draft, account), 'sent@x')
        send = next(params for method, params in calls if method == 'send')
        self.assertEqual((send['identityId'], send['email']), ('id5', 'will@work.example'))
        self.assertEqual(remembered, [(account, 'id7', 'account7')])

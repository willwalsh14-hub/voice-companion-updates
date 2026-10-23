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
        self.thunderbird = subprocess.Popen(['node', str(HERE / 'thunderbird_mail_extension' / 'fake-thunderbird.cjs')],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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

    def test_account_type_menu_offers_thunderbird(self):
        import companion
        self.assertIn(('Thunderbird', 'thunderbird'), companion.ACCOUNT_TYPES)


if __name__ == '__main__':
    unittest.main()

"""Bugs found in the October 2026 fine-comb review of email, documents, notes,
Document reader and File manager. Each test failed before its fix."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_yahoo_mail as yahoo_tests
import thunderbird_mailbox
from thunderbird_mailbox import ThunderbirdMailbox

ME = 'me@gmail.com'


def thunderbird(pages=None, context=None):
    """A Thunderbird mailbox whose add-on answers from these values."""
    def call(method, **params):
        if method == 'inbox':
            return {'id': 'account1://INBOX'}
        if method == 'list':
            return pages
        if method == 'context':
            return context
        raise AssertionError(method)
    patcher = mock.patch.object(thunderbird_mailbox, 'call', call)
    patcher.start()
    return ThunderbirdMailbox(ME, {'account_id': 'account1', 'identity_id': 'id1'}), patcher


class ThunderbirdAddressTests(unittest.TestCase):
    def use(self, pages=None, context=None):
        box, patcher = thunderbird(pages, context)
        self.addCleanup(patcher.stop)
        return box

    def test_a_sender_name_with_a_comma_does_not_hide_the_whole_list(self):
        # Thunderbird decodes "=?UTF-8?Q?Smith,_John?=" without quotes. Read as a list of
        # people, it was two, and the whole Inbox failed to open.
        box = self.use({'rows': [{'id': 7, 'from': 'Smith, John <john@example.com>', 'subject': 'Hi'},
                                 {'id': 6, 'from': 'Pat <pat@example.com>', 'subject': 'Two'},
                                 {'id': 5, 'from': '', 'subject': 'Three'}]})
        rows, _ = box.list_messages('inbox')
        self.assertEqual([(row['from'], row['sender_name']) for row in rows],
                         [('john@example.com', 'Smith, John'), ('pat@example.com', 'Pat'), ('Unknown sender', '')])

    def test_reply_all_never_adds_part_of_a_name_as_an_address(self):
        box = self.use(context={'author': 'Smith, John <john@example.com>', 'subject': 'Plans',
                                'recipients': [ME, 'Lee, Pat <pat@example.com>'], 'ccList': ['kim@example.com']})
        context = box.response_context(7, 'reply_all', ME)
        self.assertEqual((context['recipient'], context['cc']), ('john@example.com', ['pat@example.com', 'kim@example.com']))

    def test_reply_to_a_message_you_sent_goes_to_the_people_you_wrote_to(self):
        # In Sent, the sender is you. The reply went to nobody ("Recipient .").
        box = self.use(context={'author': 'Me <me@gmail.com>', 'subject': 'Plans',
                                'recipients': ['Sam <sam@example.com>'], 'ccList': ['kim@example.com']})
        self.assertEqual(box.response_context(7, 'reply', ME)['recipient'], 'sam@example.com')
        context = box.response_context(7, 'reply_all', ME)
        self.assertEqual((context['recipient'], context['cc']), ('sam@example.com', ['kim@example.com']))

    def test_reply_to_header_is_still_used(self):
        box = self.use(context={'author': 'Sam <sam@example.com>', 'replyTo': '"Help, Desk" <help@example.com>',
                                'subject': 'Ticket', 'recipients': [ME]})
        self.assertEqual(box.response_context(7, 'reply', ME)['recipient'], 'help@example.com')


class YahooReplyTests(yahoo_tests.YahooTestCase):
    def test_reply_to_a_message_you_sent_goes_to_the_people_you_wrote_to(self):
        raw = yahoo_tests.message(sender='Vet <' + yahoo_tests.ADDRESS + '>', to='Sam Lee <sam@example.com>',
                                  extra={'Cc': 'kim@example.com'})
        uid = self.server.add('Inbox', raw)
        self.assertEqual(self.mailbox().response_context(str(uid), 'reply', yahoo_tests.ADDRESS, 'inbox')['recipient'],
                         'sam@example.com')
        context = self.mailbox().response_context(str(uid), 'reply_all', yahoo_tests.ADDRESS, 'inbox')
        self.assertEqual((context['recipient'], context['cc']), ('sam@example.com', ['kim@example.com']))


class Inbox:
    """A mail client with three messages in Inbox and two folders."""
    def __init__(self, *args): pass
    def folders(self): return [('Inbox', 'inbox'), ('Receipts', 'receipts')]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': str(n), 'from': 'Sender %d <s%d@example.com>' % (n, n), 'subject': 'Subject %d' % n,
                  'date': 'October %d, 2026' % n} for n in (3, 2, 1)], None)
    def read_message(self, ident, folder=None):
        from email_reader import plain_body
        return plain_body('Body of message ' + ident + '.')
    def response_context(self, ident, action, own, folder=None):
        return {'action': action, 'source_id': ident, 'recipient': '', 'cc': [], 'subject': 'Re: Notice'}


class MailSessionTests(unittest.TestCase):
    def session(self):
        from mail_voice import MailSession
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        patcher = mock.patch('mail_voice.account_token', return_value=(ME, 'token'))
        patcher.start(); self.addCleanup(patcher.stop)
        session = MailSession('thunderbird', folder.name, Inbox)
        session.process('open inbox')
        return session

    def test_going_back_from_a_message_says_that_message_not_the_whole_page(self):
        session = self.session()
        session.process('next message')
        self.assertIn('Body of message 2', session.process('open current message'))
        said = session.process('back to folder')
        self.assertEqual(said, 'Back to Inbox. Sender 2. Subject 2. October 2, 2026. 2 of 3.')

    def test_folder_list_ends_are_said_like_every_other_list(self):
        session = self.session()
        session.process('go to folder')
        self.assertEqual(session.process('previous'), 'Start of list. Folder 1 of 2: Inbox.')
        session.process('next')
        self.assertEqual(session.process('next'), 'End of list. Folder 2 of 2: Receipts.')


class CompanionEmailTests(unittest.TestCase):
    def setUp(self):
        import companion
        self.app = companion
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        self.said = []
        for name in ('speak', 'speak_prompt', 'speak_status', 'dictation_feedback'):
            patcher = mock.patch.object(companion, name, side_effect=lambda text, *a, **k: self.said.append(text))
            patcher.start(); self.addCleanup(patcher.stop)
        for name, value in (('APP', self.folder), ('email_draft', None), ('mail_session', None), ('pending_send', None),
                            ('APP_WINDOW', None), ('focus_email_entry', lambda: None)):
            patcher = mock.patch.object(companion, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch('mail_voice.account_token', return_value=(ME, 'token'))
        patcher.start(); self.addCleanup(patcher.stop)

    def open_inbox(self):
        from mail_voice import MailSession
        self.app.mail_session = MailSession('thunderbird', self.folder, Inbox)
        self.app.mail_session.process('open inbox')

    def test_saving_a_draft_back_to_the_inbox_says_the_folder_once(self):
        from email_draft import VoiceEmail
        self.open_inbox()
        self.app.email_draft = VoiceEmail(self.folder / 'Email Drafts', recipient='sam@example.com', subject='Hi')
        self.assertEqual(self.app.handle('leave email', 'email_draft'), 'mailbox')
        self.assertEqual(self.said[-1], 'Email draft saved. Back to Inbox. Sender 3. Subject 3. October 3, 2026. 1 of 3.')

    def test_after_sending_the_folder_name_is_said_once(self):
        from document_editor import Paragraph
        from email_draft import VoiceEmail
        self.open_inbox()
        draft = VoiceEmail(self.folder / 'Email Drafts', recipient='sam@example.com', subject='Hi', provider='thunderbird')
        draft.paragraphs = [Paragraph(text='Hello.')]
        self.app.email_draft = draft
        with mock.patch.object(self.app.ProtectedStore, 'load', return_value={'address': ME}), \
             mock.patch.object(self.app, 'account_token', return_value=(ME, 'token')), \
             mock.patch.object(self.app, 'submit', return_value='accepted'):
            self.app.handle('send it', 'email_draft')
            self.assertEqual(self.app.handle('yes', 'email_draft'), 'mailbox')
        self.assertTrue(self.said[-1].startswith('Back to Inbox. Sender 3.'), self.said[-1])

    def test_a_reply_with_no_address_asks_for_one(self):
        self.open_inbox()
        self.app.mail_session.process('open current message')
        self.assertEqual(self.app.handle('reply', 'mailbox'), 'email_draft')
        self.assertNotIn('Recipient .', self.said[-1])
        self.assertIn('no address to reply to. Say email to', self.said[-1])
        self.assertEqual(self.app.email_draft.compose_step, 'recipient')

    def test_sending_with_no_recipient_says_so(self):
        from email_delivery import message_parts
        from email_draft import VoiceEmail
        draft = VoiceEmail(self.folder / 'Email Drafts', subject='Hi')
        with self.assertRaisesRegex(ValueError, 'This email has no recipient. Say email to'):
            message_parts(draft)


if __name__ == '__main__':
    unittest.main()

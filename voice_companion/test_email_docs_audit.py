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


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, self.folder, True)

    def document(self, *paragraphs):
        from document_editor import VoiceDocument
        document = VoiceDocument(self.folder, title='Letter')
        document.paragraphs = list(paragraphs)
        return document

    def test_a_file_locked_by_word_does_not_stop_the_app(self):
        # Saving after each change failed while Word had the file open; the error stopped Companion.
        import companion
        from document_editor import Paragraph, VoiceDocument
        document = self.document(Paragraph('Hello.'))
        document.dictating = True
        said = []
        with mock.patch.object(companion, 'document', document), \
             mock.patch.object(companion, 'speak', side_effect=lambda text, *a, **k: said.append(text)), \
             mock.patch.object(companion, 'dictation_feedback', side_effect=said.append), \
             mock.patch.object(VoiceDocument, 'save', side_effect=PermissionError(13, 'Permission denied')):
            self.assertEqual(companion.handle('More words here', 'document'), 'document')
        self.assertIn('could not be saved. It may be open in another program', said[-1])
        self.assertIn('More words here', document.paragraphs[0].text)

    def test_heading_list_names_the_level_not_the_paragraph_number_as_a_level(self):
        from document_editor import Paragraph
        document = self.document(Paragraph('Title', kind='Heading 1'), Paragraph('Text'), Paragraph('Plans', kind='Heading 2'))
        self.assertEqual(document.process('list headings'),
                         'Title, heading level 1, paragraph 1. Plans, heading level 2, paragraph 3.')

    def test_moving_past_the_first_or_last_paragraph_says_so(self):
        from document_editor import Paragraph
        document = self.document(Paragraph('One.'), Paragraph('Two.'))
        self.assertEqual(document.process('next paragraph'), 'Two.')
        self.assertEqual(document.process('next paragraph'), 'End of document. Two.')
        self.assertEqual(document.process('previous paragraph'), 'One.')
        self.assertEqual(document.process('previous paragraph'), 'Beginning of document. One.')


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, self.folder, True)

    def test_damaged_files_are_reported_never_stop_the_app(self):
        # From Recent documents in Document reader, a damaged DOCX stopped Companion (BadZipFile).
        import types
        from task_navigation import TaskNavigation
        for name, data in (('report.docx', b'not a zip'), ('book.epub', b''), ('notes.odt', b'PK\x03\x04broken')):
            (self.folder / name).write_bytes(data)
            said = []
            app = types.SimpleNamespace(speak=said.append, PREFERENCES={}, APP=self.folder, FILE_READER=None)
            self.assertEqual(TaskNavigation(app).read_file(self.folder / name), 'reader_menu', name)
            self.assertEqual(said[0], 'This file is damaged or empty, so it could not be opened.', name)

    def test_a_locked_file_is_named_in_plain_words(self):
        import document_reader
        path = self.folder / 'notes.txt'
        path.write_text('Words')
        with mock.patch.object(Path, 'read_bytes', side_effect=PermissionError(13, 'Permission denied')):
            with self.assertRaises(ValueError) as caught:
                document_reader.open_document(path)
        self.assertEqual(str(caught.exception), 'notes.txt is open in another program, or Windows did not let '
                                                'Voice Companion read it. Close it there, then try again.')

    def test_a_word_document_open_elsewhere_is_explained(self):
        import types
        import zipfile
        from task_navigation import TaskNavigation
        path = self.folder / 'Letter.docx'
        path.write_bytes(b'x')
        app = types.SimpleNamespace(speak=lambda text: None, PREFERENCES={}, APP=self.folder, document=None)
        with mock.patch.object(zipfile, 'ZipFile', side_effect=PermissionError(13, 'Permission denied')):
            with self.assertRaisesRegex(ValueError, '^Letter.docx is open in another program'):
                TaskNavigation(app).open_document(path)


class FileManagerTests(unittest.TestCase):
    def manager(self, names):
        import time
        from file_manager import FileManager, Item
        class Files:
            def roots(self): return [Item('Data', '\\\\server\\data', True, True)]
            def list(self, path): return [Item(name, path + '\\' + name) for name in names]
        manager = FileManager(Files())
        manager.refresh('\\\\server\\data')
        until = time.monotonic() + 5
        while manager.busy and time.monotonic() < until:
            said = manager.poll() or ''
            time.sleep(.005)
        return manager, said

    def test_a_network_share_has_a_name(self):
        from file_manager import location_name
        self.assertEqual(location_name('\\\\server\\data'), 'data on server')
        manager, said = self.manager(['a.txt'])
        self.assertTrue(said.startswith('data on server. a.txt, file. 1 of 1.'), said)

    def test_the_end_of_a_one_item_list_is_the_end(self):
        manager, _ = self.manager(['a.txt'])
        self.assertTrue(manager.command('next').startswith('End of list.'))
        self.assertTrue(manager.command('previous').startswith('Start of list.'))

    def test_an_empty_folder_says_only_that_it_is_empty(self):
        manager, said = self.manager([])
        self.assertEqual(manager.command('next'), 'This folder is empty.')
        self.assertEqual(manager.command('end'), 'This folder is empty.')

    def test_menus_say_the_end_of_a_one_item_list(self):
        import types
        from task_navigation import TaskNavigation
        said = []
        nav = TaskNavigation(types.SimpleNamespace(speak=said.append, normalized_command=lambda text: text,
                                                   spoken_control=lambda text: False,
                                                   NAMED_LIST=None, document=None, email_draft=None))
        nav.reader_recent = [str(Path('docs') / 'report.txt')]
        nav.handle('next', 'reader_recent')
        self.assertTrue(said[-1].startswith('End of list. report.txt'), said[-1])


if __name__ == '__main__':
    unittest.main()

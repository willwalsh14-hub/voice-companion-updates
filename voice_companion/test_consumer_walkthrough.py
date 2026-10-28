"""Problems found by using the app as a customer would (October 28, 2026).

Each test drives the real app with the words or keys a person would use, and
checks what they hear."""
import unittest
from unittest.mock import Mock, patch

import companion
from document_editor import Paragraph
from test_core_email_fixes import CompanionTestCase


class SendReviewTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        store = Mock(); store.load.return_value = {'address': 'me@gmail.com'}
        store.selected.return_value = ('thunderbird', 'me@gmail.com'); store.accounts.return_value = [('thunderbird', 'me@gmail.com')]
        for target, value in (('ProtectedStore', Mock(return_value=store)),
                              ('account_token', Mock(return_value=('me@gmail.com', 'tok'))),
                              ('MailSession', Mock()), ('finish_mail_announcement', Mock())):
            p = patch.object(companion, target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'submit'); self.submit = p.start(); self.addCleanup(p.stop)

    def review(self, body, subject='Hi', **fields):
        draft = self.draft(subject=subject, **fields)
        draft.paragraphs = [Paragraph(body)]
        companion.handle('send email', 'email_draft', typed=True)
        return self.said[-1]

    def test_review_never_doubles_the_message_punctuation(self):
        # Heard: "Message: Sounds good.. Is that right?" A screen reader can say "dot dot".
        review = self.review('Sounds good.')
        self.assertIn('Message: Sounds good. Is that right?', review)
        self.assertNotIn('..', review)

    def test_review_keeps_a_question_or_exclamation_as_written(self):
        review = self.review('Are you free?', subject='Lunch?')
        self.assertIn('Subject Lunch? Message: Are you free? Is that right?', review)
        self.assertNotIn('?.', review)

    def test_review_of_a_reply_adds_one_period_before_the_note(self):
        review = self.review('Sounds good.', response_context={'action': 'reply', 'source_id': '1'})
        self.assertIn('Message: Sounds good. This reply uses the original message in your mailbox. Is that right?', review)

    def test_review_of_a_body_without_punctuation_still_ends_the_sentence(self):
        self.assertIn('Message: See you soon. Is that right?', self.review('See you soon'))


class OpenInboxWordsTests(CompanionTestCase):
    """A new user says what they want, not the menu name: "Open the inbox." used to get
    "Say ... email to open the selected inbox." and nothing opened."""
    PHRASES = ('Open the inbox.', 'open my inbox', 'Inbox.', 'Go to my inbox.', 'Check my email.',
               'Check my mail.', 'Read my mail.', 'Open my email.', 'Go to email.')

    def setUp(self):
        super().setUp()
        store = Mock(); store.selected.return_value = ('yahoo', 'vet@yahoo.com')
        self.session = Mock(); self.session.process.return_value = 'Inbox. Mom. Dinner. 1 of 1.'
        for target, value in (('ProtectedStore', Mock(return_value=store)), ('MailSession', Mock(return_value=self.session))):
            p = patch.object(companion, target, value); p.start(); self.addCleanup(p.stop)

    def test_everyday_inbox_words_open_the_inbox_from_the_main_menu(self):
        for phrase in self.PHRASES:
            with self.subTest(phrase=phrase):
                self.said.clear()
                self.assertEqual(companion.handle(phrase, 'awake'), 'mailbox')
                self.assertEqual(self.said[-1], 'Inbox. Mom. Dinner. 1 of 1.')

    def test_the_inbox_and_web_pages_keep_their_own_inbox_words(self):
        session = Mock(); session.process.return_value = 'Inbox folder.'
        web = Mock(); web.command.return_value = 'Opened the Inbox link.'; web.snapshot = {'title': 'Mail'}
        with patch.object(companion, 'mail_session', session), patch.object(companion, 'web_session', web):
            companion.handle('inbox', 'mailbox')
            session.process.assert_called_with('inbox')
            companion.handle('open inbox', 'web')
            web.command.assert_called_with('open inbox')

    def test_the_same_words_are_text_in_a_document(self):
        self.open_document()
        for phrase in self.PHRASES:
            with self.subTest(phrase=phrase):
                self.assertEqual(companion.handle(phrase, 'document'), 'document')
        self.assertIn('Check my mail.', companion.document_text(companion.document))


class OneAttachmentWordsTests(unittest.TestCase):
    def test_one_file_is_it_not_them(self):
        # Heard: "1 attachment: Benefit Letter.pdf, 16 bytes. Press L to list them."
        from mail_voice import MailSession
        session = MailSession.__new__(MailSession)
        session.attachments = [{'id': '2', 'name': 'Benefit Letter.pdf', 'size': 16}]
        self.assertEqual(session._attachment_note(), '1 attachment: Benefit Letter.pdf, 16 bytes. Press L to list it. ')
        session.attachments.append({'id': '3', 'name': 'photo.jpg', 'size': 14})
        self.assertTrue(session._attachment_note().endswith(' Press L to list them. '))

class LeavingANoteTests(CompanionTestCase):
    """Leaving a note is said once, and truthfully.

    Heard before: Escape gave "Saved your note." and then "Note saved. Notepad menu...",
    which cut the first one off. Main menu gave three messages. An empty note said
    "Note saved." though nothing was saved."""

    def setUp(self):
        super().setUp()
        for name, value in (('note_buffer', []), ('OPEN_NOTE', None), ('MAIN_MENU_LEAD', '')):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'sync_app_context'); p.start(); self.addCleanup(p.stop)

    def notes(self):
        import notepad
        return [n['text'] if isinstance(n, dict) else str(n) for n in notepad.read_notes(self.folder)]

    def leave(self, words, how):
        companion.handle('write a note', 'awake')
        for text in words:companion.handle(text, 'note')
        self.said.clear()
        if how == 'Escape':return companion.handle_keyboard(companion.navigation_key('Escape', 'note'), 'note')
        return companion.handle(how, 'note')

    def test_escape_with_words_says_saved_once_then_the_menu(self):
        self.assertEqual(self.leave(['Buy milk.'], 'Escape'), 'notepad_menu')
        self.assertEqual(len(self.said), 1, self.said)
        self.assertTrue(self.said[0].startswith('Saved your note. Notepad menu. Create a note'), self.said)
        self.assertIn('Buy milk.', ' '.join(self.notes()))

    def test_main_menu_with_words_is_one_message(self):
        self.assertEqual(self.leave(['Call Bob.'], 'Main menu.'), 'awake')
        self.assertEqual(len(self.said), 1, self.said)
        self.assertTrue(self.said[0].startswith('Saved your note. Main menu.'), self.said)

    def test_an_empty_note_does_not_claim_it_was_saved(self):
        for how in ('Escape', 'Main menu.', 'Go back.'):
            with self.subTest(how=how):
                self.leave([], how)
                self.assertEqual(len(self.said), 1, self.said)
                self.assertNotIn('saved', self.said[0].split('.')[0].lower().replace('nothing was saved', ''))
                self.assertTrue(self.said[0].startswith('The note was empty, so nothing was saved.'), self.said)
        self.assertEqual(self.notes(), [])

    def test_a_note_that_cannot_be_saved_keeps_the_words_and_says_so(self):
        import notepad
        companion.handle('write a note', 'awake'); companion.handle('Keep me.', 'note')
        self.said.clear()
        with patch.object(notepad, 'add', side_effect=OSError('locked')):
            self.assertEqual(companion.handle('Main menu.', 'note'), 'note')
        self.assertEqual(len(self.said), 1, self.said)
        self.assertIn('could not be saved', self.said[0])
        self.assertEqual(companion.note_buffer, ['Keep me.'])


class ReadMyNotesTests(CompanionTestCase):
    def test_read_my_notes_lists_saved_notes_instead_of_starting_a_new_one(self):
        # Heard: "Read note." on the Notepad menu said "Dictation started." and began an empty note.
        for mode in ('awake', 'notepad_menu'):
            for phrase in ('Read note.', 'Read my notes.', 'read notes'):
                with self.subTest(mode=mode, phrase=phrase), patch.object(companion, 'list_saved_notes') as listed:
                    self.assertEqual(companion.handle(phrase, mode), mode)
                    listed.assert_called_once()


class SlowDriveTests(CompanionTestCase):
    """A slow or network drive: keys pressed while File manager is still loading.

    Heard before: Down said "End of list." and Escape said "Opening canceled. This folder
    is empty." while the drive list had not loaded yet."""

    def setUp(self):
        super().setUp()
        import threading
        import file_manager
        from test_file_manager import FakeFiles
        self.release = threading.Event(); self.addCleanup(self.release.set)
        (self.folder / 'drive').mkdir(); (self.folder / 'drive' / 'a.txt').write_text('A')
        backend = FakeFiles(self.folder / 'drive')
        slow_roots = backend.roots
        backend.roots = lambda: (self.release.wait(10), slow_roots())[1]
        for name, value in (('FILE_MANAGER', None), ('CONFIRM_CHOICE', 'no')):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(file_manager, 'ShellFiles', lambda: backend); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'sync_app_context'); p.start(); self.addCleanup(p.stop)
        import regular_browser
        p = patch.object(regular_browser, 'data_folder', lambda: self.folder); p.start(); self.addCleanup(p.stop)

    def key(self, name, mode='files'):
        request = companion.navigation_key(name, mode)
        return companion.handle_keyboard(request, mode) if request else mode

    def test_arrows_while_loading_say_please_wait(self):
        self.assertEqual(companion.handle('file manager', 'awake'), 'files')
        self.assertEqual(self.said[-1], 'File manager. Loading drive list.')
        for name in ('Down', 'Up', 'Home', 'End'):
            with self.subTest(key=name):
                self.key(name)
                self.assertEqual(self.said[-1], 'File manager is working. Please wait.')

    def test_escape_while_the_first_list_loads_does_not_say_the_folder_is_empty(self):
        companion.handle('file manager', 'awake')
        self.assertEqual(self.key('Escape'), 'files')
        self.assertNotIn('empty', self.said[-1])
        self.assertTrue(self.said[-1].startswith('Opening canceled.'), self.said[-1])


class FileActionWordsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from file_manager import FileManager
        from test_file_manager import FakeFiles, settle
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name); (root / 'Photos').mkdir(); (root / 'a.txt').write_text('A'); (root / 'b.txt').write_text('B')
        self.manager = FileManager(FakeFiles(root)); self.settle = settle
        self.manager.refresh(str(root)); settle(self.manager)

    def test_one_item_is_singular(self):
        # Heard: "Working on 1 items."
        self.manager.index = 1
        self.manager.command('rename')
        self.assertEqual(self.manager.command('Notes.txt'), 'Working on 1 item.')
        self.settle(self.manager)
        self.manager.command('select all')
        self.assertEqual(self.manager.command('delete').split('?')[0], 'Delete Photos, a.txt, b.txt')
        self.assertEqual(self.manager.command('yes'), 'Working on 3 items.')

    def test_renaming_a_folder_does_not_ask_for_an_extension(self):
        # A folder has no extension; asking for one confuses a new user.
        self.manager.index = 0
        self.assertEqual(self.manager.command('rename'), 'Rename Photos. Type or say the new name, then press Enter.')
        self.manager.command('cancel'); self.manager.index = 1
        self.assertEqual(self.manager.command('rename'),
                         'Rename a.txt. Type or say the new name, including the extension, then press Enter.')


class FileManagerUnknownWordsTests(FileActionWordsTests):
    def test_unknown_words_say_what_was_heard_and_where_help_is(self):
        # Heard: "Read document." -> "No item has that name in this list." It did not say
        # what it heard, or that F1 lists the commands.
        with self.assertRaises(ValueError) as caught:
            self.manager.command('Read document.')
        self.assertEqual(str(caught.exception), 'No item is named Read document. Say an item name, or press F1 for help.')
        self.assertEqual(self.manager.command('a.txt'), 'Opening a.txt.')

    def test_a_spoken_name_with_a_recognizer_period_still_matches(self):
        # Recognizers add a period: "Photos." or "Open b." must find the item.
        self.assertEqual(self.manager.command('Photos.'), 'Opening folder.')
        self.settle(self.manager); self.manager.command('go back'); self.settle(self.manager)
        self.assertEqual(self.manager.command('Open b.'), 'Opening b.txt.')


class ThatOneMeansTheHighlightedChoiceTests(CompanionTestCase):
    """In a yes or no question, "that one" picks the choice you are on, as Enter does.

    Heard before: Exit and Shut down start on No. Enter answered No, but saying
    "That one." answered Yes: it closed the app or shut down the computer."""

    def setUp(self):
        super().setUp()
        for name, value in (('CONFIRM_CHOICE', 'yes'), ('EXIT_RETURN_MODE', 'awake'), ('POWER_ACTION', None),
                            ('POWER_RETURN_MODE', 'awake'), ('MAIN_MENU_LEAD', '')):
            p = patch.object(companion, name, value, create=True); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'sync_app_context'); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'finish_exit', return_value='exit'); self.exit = p.start(); self.addCleanup(p.stop)
        import windows_actions
        p = patch.object(windows_actions, 'request_power'); self.power = p.start(); self.addCleanup(p.stop)

    def key(self, name, mode):
        request = companion.navigation_key(name, mode)
        return companion.handle_keyboard(request, mode) if request else mode

    def test_exit_starts_on_no_and_that_one_keeps_the_app_open(self):
        self.assertEqual(self.key('Escape', 'awake'), 'exit_confirm')
        for phrase in ('That one.', 'confirm that'):
            with self.subTest(phrase=phrase):
                self.assertEqual(companion.handle(phrase, 'exit_confirm'), 'awake')
                self.exit.assert_not_called()
                self.key('Escape', 'awake')
        self.key('Up', 'exit_confirm')
        self.assertEqual(companion.handle('That one.', 'exit_confirm'), 'exit')

    def test_shut_down_starts_on_no_and_that_one_does_not_shut_down(self):
        with patch.dict(companion.PREFERENCES, ask_before_shutdown=True):
            self.assertEqual(companion.handle('shut down computer', 'awake', typed=True), 'power_confirm')
            self.assertEqual(companion.handle('That one.', 'power_confirm'), 'awake')
        self.power.assert_not_called()

    def test_yes_and_okay_still_answer_yes(self):
        self.key('Escape', 'awake')
        self.assertEqual(companion.handle('Yes.', 'exit_confirm'), 'exit')

    def test_save_question_on_no_with_that_one_discards(self):
        doc = self.open_document(); doc.title = 'Letter'
        with patch.object(companion, 'discard_document_changes') as discard, patch.object(type(doc), 'save') as save:
            self.assertEqual(companion.handle('leave document', 'document', typed=True), 'document_save')
            self.key('Down', 'document_save')
            companion.handle('That one.', 'document_save')
        discard.assert_called_once(); save.assert_not_called()


if __name__ == '__main__':
    unittest.main()

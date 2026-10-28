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


class ReaderOpeningWordsTests(FileActionWordsTests):
    def test_ocr_is_mentioned_only_for_a_pdf(self):
        # Heard: "Opening blank.txt. Scanned pages are read with offline OCR." OCR is jargon,
        # and only PDF pages can be scanned.
        root = __import__('pathlib').Path(self.manager.location)
        (root / 'scan.pdf').write_bytes(b'%PDF-1.4')
        self.manager.refresh(str(root)); self.settle(self.manager)
        self.manager.pick_extensions = ('.txt', '.pdf'); self.manager.pick_label = 'document'
        self.manager.choose('a.txt')
        self.assertEqual(self.manager.command('open'), 'Opening a.txt.')
        self.settle(self.manager)
        self.manager.choose('scan.pdf')
        self.assertEqual(self.manager.command('open'), 'Opening scan.pdf. Scanned pages are read with offline OCR.')
        self.settle(self.manager)


class ReaderPickerTests(CompanionTestCase):
    """Document reader, Open a document, then a text file or PDF.

    Before: it was not added to Recent documents, and Escape went to File manager
    (three more Escapes to get back). A Word file went straight back to the menu."""

    def setUp(self):
        super().setUp()
        import file_manager, regular_browser
        from test_file_manager import FakeFiles, settle
        drive = self.folder / 'drive'; drive.mkdir(); (drive / 'notes.txt').write_text('Buy milk.')
        self.settle = settle
        for target, name, value in ((file_manager, 'ShellFiles', lambda: FakeFiles(drive)),
                                    (regular_browser, 'data_folder', lambda: self.folder),
                                    (companion, 'FILE_MANAGER', None), (companion, 'FILE_READER', None),
                                    (companion, 'sync_app_context', Mock())):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        import task_navigation
        nav = task_navigation.TaskNavigation(companion)
        p = patch.object(companion, 'task_navigation', lambda: nav); p.start(); self.addCleanup(p.stop)

    def step(self, key, mode):
        request = companion.navigation_key(key, mode)
        mode = companion.handle_keyboard(request, mode) if request else mode
        self.settle(companion.FILE_MANAGER)
        return companion.poll_file_manager(mode)

    def test_a_text_file_returns_to_the_reader_menu_and_is_recent(self):
        mode = companion.handle('document reader', 'awake')
        mode = self.step('Enter', mode)              # Open a document: drive list
        mode = self.step('Enter', mode)              # the drive
        mode = self.step('Enter', mode)              # notes.txt
        self.assertEqual(mode, 'file_reader')
        self.assertIn('Buy milk.', self.said[-1])
        self.assertEqual(self.step('Escape', mode), 'reader_menu')
        self.assertTrue(self.said[-1].startswith('Document reader menu.'), self.said[-1])
        self.assertEqual([__import__('pathlib').Path(p).name for p in companion.task_navigation().reader_list()], ['notes.txt'])


class ContactPhoneTests(__import__('test_contacts_screen').CompanionHarness):
    """Heard: a name typed into the phone step was saved as the phone ("John Smith, John Smith").
    Spoken digits arrive as words from the recognizer ("five five five one two three four")."""
    thunderbird = False

    def add(self, phone):
        mode = companion.handle('open contacts', 'awake')
        mode = companion.handle('add contact', mode)
        mode = companion.handle('John Smith', mode)
        mode = companion.handle('skip', mode)
        return companion.handle(phone, mode)

    def phone(self):
        from address_book import AddressBook
        return AddressBook(self.root).get('john smith')['phone']

    def test_words_that_are_not_a_phone_number_are_asked_again(self):
        self.assertEqual(self.add('John Smith'), 'task_input')
        self.assertEqual(self.said(), 'That does not look like a phone number. Type or say the digits, '
                                      'then press Enter. To leave it out, press Enter or say skip.')
        self.assertEqual(companion.handle('555 0100', 'task_input'), 'contacts')
        self.assertEqual(self.phone(), '555 0100')

    def test_spoken_digit_words_are_saved_as_digits(self):
        self.assertEqual(self.add('Five five five, oh one two three.'), 'contacts')
        self.assertEqual(self.phone(), '555 0123')

    def test_typed_numbers_are_kept_as_typed(self):
        self.add('(555) 010-0199')
        self.assertEqual(self.phone(), '(555) 010-0199')

    def test_editing_the_phone_checks_it_too(self):
        self.add('555 0100')
        mode = companion.handle('change the phone to Main Street', 'contacts')
        self.assertEqual(mode, 'task_input')
        self.assertTrue(self.said().startswith('That does not look like a phone number.'), self.said())


class AddEventFromMainMenuTests(__import__('test_calendar_screen').CalendarHarness):
    def test_add_an_event_from_the_main_menu_is_one_message(self):
        # Heard: the whole Calendar announcement, then "New event. When is it?" at once,
        # so the first was cut off by the second.
        self.speech.reset_mock()
        self.assertEqual(companion.handle('Add an event.', 'awake'), 'task_input')
        self.assertEqual([str(c.args[0]) for c in self.speech.call_args_list],
                         ['New event. When is it? For example, say tomorrow at 3 PM, or Friday all day. Escape cancels.'])

    def test_everything_at_once_is_one_message_too(self):
        self.speech.reset_mock()
        self.assertEqual(companion.handle('Add appointment tomorrow at 3 PM called doctor.', 'awake'), 'calendar')
        self.assertEqual(len(self.speech.call_args_list), 1, self.speech.call_args_list)


class TutorialQuestionsTests(unittest.TestCase):
    def setUp(self):
        from practice_tutorial import PracticeTutorial
        self.tutorial = PracticeTutorial(); self.tutorial.start()

    def test_what_can_i_say_lists_the_lesson_commands(self):
        # Heard: "No explanation found for what can i say. Say lesson list, ..."
        for phrase in ('What can I say?', 'what can I do', 'What do I say?'):
            with self.subTest(phrase=phrase):
                said, finished = self.tutorial.process(phrase)
                self.assertFalse(finished)
                self.assertTrue(said.startswith('Use Up and Down to choose, and Enter to open. Say next lesson,'), said)
        self.tutorial.process('word processor')
        said, _ = self.tutorial.process('What can I say?')
        self.assertIn('Say next lesson,', said)
        self.assertEqual(self.tutorial.process('repeat')[0], said)

    def test_a_search_that_finds_nothing_repeats_the_words_as_said(self):
        said, _ = self.tutorial.process('explain command Zebra Crossing')
        self.assertTrue(said.startswith('No explanation found for Zebra Crossing.'), said)


class FirstTimeEmailTests(CompanionTestCase):
    def test_email_with_no_account_points_only_to_add_account(self):
        # Heard: "No email account is selected. Say add account, or list email accounts."
        # With no account at all, list email accounts only says there are none.
        store = Mock(); store.selected.return_value = None; store.accounts.return_value = []
        with patch.object(companion, 'ProtectedStore', Mock(return_value=store)):
            for phrase in ('Email.', 'check email', 'Open the inbox.'):
                with self.subTest(phrase=phrase):
                    self.assertEqual(companion.handle(phrase, 'awake'), 'awake')
                    self.assertEqual(self.said[-1], 'No email account is set up yet. Say add account to set one up.')
            store.accounts.return_value = [('yahoo', 'vet@yahoo.com'), ('thunderbird', 'me@gmail.com')]
            companion.handle('Email.', 'awake')
            self.assertEqual(self.said[-1], 'No email account is selected. Say add account, or list email accounts.')


    def test_canceling_a_draft_with_no_account_is_two_messages_not_three(self):
        store = Mock(); store.selected.return_value = None; store.accounts.return_value = []
        with patch.object(companion, 'ProtectedStore', Mock(return_value=store)), \
                patch.object(companion, 'finish_mail_announcement'), patch.object(companion, 'MAIN_MENU_LEAD', ''):
            self.assertEqual(companion.handle('Write an email.', 'awake'), 'email_draft')
            self.said.clear()
            self.assertEqual(companion.handle('cancel', 'email_draft'), 'awake')
        self.assertEqual(len(self.said), 2, self.said)
        self.assertEqual(self.said[0], 'Draft canceled without saving.')
        self.assertTrue(self.said[1].startswith('No email account is set up yet. Main menu.'), self.said)


    def test_gmail_through_thunderbird_is_one_message(self):
        # Heard: "Gmail works through Thunderbird." then the next question at once, cutting it off.
        step = Mock(question='install_program', message='Install Thunderbird? Say yes or no.')
        flow = Mock(); flow.start.return_value = step
        with patch.object(companion, 'new_thunderbird_setup', Mock(return_value=flow)), \
                patch.object(companion, 'THUNDERBIRD_SETUP', None), patch.object(companion, 'account_setup', False), \
                patch.object(companion, 'sync_app_context'):
            for phrase in ('Add my Gmail account.', 'add account|Gmail.'):
                with self.subTest(phrase=phrase):
                    self.said.clear(); companion.THUNDERBIRD_SETUP = None
                    for part in phrase.split('|'):companion.handle(part, 'awake')
                    self.assertEqual(self.said[-1], 'Gmail works through Thunderbird. Install Thunderbird? Say yes or no.')
                    self.assertNotIn('Gmail works through Thunderbird.', self.said[:-1])
                    # Repeat says the question alone.
                    self.assertEqual(companion.THUNDERBIRD_SETUP['message'], 'Install Thunderbird? Say yes or no.')


class LargeTextLayoutTests(unittest.TestCase):
    """400 percent text, full screen off, on a 1024 by 768 screen: the title and the last
    message took the whole window. The list got no room, and the command box, which has
    the keyboard at the main menu, was pushed off the window (Windows drops keys sent to it)."""
    # Heights measured in the real window at 400 percent (pixels).
    SIZES = dict(heading=272, entry=109, label=55, message_line=129, list_line=74)

    def plan(self, height, message_lines=2):
        from display_options import fit_plan
        s = self.SIZES
        return fit_plan(height, s['heading'], s['entry'], s['label'], s['message_line'], message_lines,
                        least=s['list_line'] + 24, wanted=s['list_line'] * 4 + 24)

    def used(self, plan):
        lines, label, rows = plan
        s = self.SIZES
        return s['heading'] + 14 + s['entry'] + (s['message_line'] * lines + 8 if lines else 0) + (s['label'] + 6 if label else 0) + rows

    def test_a_short_window_keeps_the_command_box_and_one_list_line(self):
        plan = self.plan(614)
        self.assertGreaterEqual(plan[2], self.SIZES['list_line'] + 24)
        self.assertLessEqual(self.used(plan), 614)

    def test_the_message_gives_way_first_then_the_label(self):
        self.assertEqual(self.plan(2000)[:2], (2, True))
        self.assertEqual(self.plan(272 + 14 + 109 + 129 + 8 + 55 + 6 + 98)[:2], (1, True))
        self.assertEqual(self.plan(272 + 14 + 109 + 55 + 6 + 98)[:2], (0, True))
        self.assertEqual(self.plan(272 + 14 + 109 + 98)[:2], (0, False))

    def test_a_tall_window_keeps_everything(self):
        self.assertEqual(self.plan(1400, 3), (3, True, 74 * 4 + 24))


if __name__ == '__main__':
    unittest.main()

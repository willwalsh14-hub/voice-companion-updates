"""Alt menus on every main screen: Email, email drafts, Notepad, the web page,
Help, lessons, Document reader, plus Alt on the main menu, Web browser menu and
Radio. Every item must run a command its screen really handles."""
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import companion as app
from field_selection import FieldSelection
from reading_navigation import ReadingCursor
from screen_menus import MENUS, ALT_SCREENS, alt_menu_key, overlay_key, items, menu_path

GUIDE = Path(__file__).with_name('START HERE - Veteran.txt')
GUIDE_HTML = Path(__file__).with_name('START HERE - Veteran.html')
NONSENSE = 'zebra quantum lantern'
# Generic answers that mean a screen did not know the command.
FALLBACKS = ('I can write a note or document', 'Say go to folder to choose from a list',
             'Document reader. Use arrows to read', 'I could not complete that mail request',
             'Email could not complete that request', 'Choose a number from')


class FakeMailClient:
    """Behaves like mailbox_access.MailboxClient for one small Gmail inbox."""
    def __init__(self, *args): pass
    def folders(self):
        return [('Inbox', 'INBOX'), ('Sent Mail', 'SENT'), ('Drafts', 'DRAFT'), ('Trash', 'TRASH'), ('Spam', 'SPAM'), ('Saved', 'Label_1')]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': 'm1', 'from': 'Sam <sam@example.com>', 'subject': 'Lunch'},
                 {'id': 'm2', 'from': 'Ann <ann@example.com>', 'subject': 'Plans'}], None)
    def read_message(self, ident, folder_id=None): return 'Hello from Sam.'
    def response_context(self, ident, action, address, folder_id=None):
        return {'action': action, 'recipient': '' if action == 'forward' else 'sam@example.com', 'cc': [],
                'subject': 'Re: Lunch', 'source_id': ident}
    def folder_info(self, ident): return {'messagesTotal': 0}
    def trash(self, ids): pass
    def move(self, ids, source, destination): pass


class WebStandIn(MagicMock):
    """Stand-in for the web session; it records what it was asked to do."""
    def __init__(self, *args, **kwargs):
        super().__init__(**{k: v for k, v in kwargs.items() if k not in ('progress',)})
        self.command.side_effect = lambda text, *a, **k: 'web handled: ' + str(text)
        self.keyboard.side_effect = lambda text, *a, **k: 'web key handled: ' + str(text)
        self.menu.process.return_value = ('menu handled', None)
        self.menu.start.return_value = 'Web browser menu.'
    def _get_child_mock(self, **kwargs):
        return MagicMock(**kwargs)


class ScreenMenuTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None, 'VOICE_PICK_ORIGINAL': None,
                    'help_session': None, 'help_return_mode': 'awake', 'tutorial_session': None, 'MEDIA_HUB': None,
                    'MEDIA_NAV': None, 'note_buffer': [], 'note_selection': FieldSelection(), 'note_reading': ReadingCursor(),
                    'OPEN_NOTE': None, 'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {}, 'PREFERENCES': dict(app.PREFERENCES),
                    'SLEEP_RETURN_MODE': 'awake', 'SEARCH_RETURN_MODE': 'awake', 'FILE_READER': None,
                    'FILE_MANAGER': None, 'TASK_NAV': None, 'SCREEN_MENU': None, 'SETTINGS_PANEL': None,
                    'ACCOUNT_ENTRY': None, 'SETTINGS_RETURN_MODE': 'awake', 'CONFIRM_CHOICE': 'yes',
                    'MEDIA_SECTION': 'radio', 'ACCOUNT_MENU_INDEX': None, 'DOCUMENT_CLOSE_TARGET': app.DOCUMENT_CLOSE_TARGET}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.said = []
        speak = lambda text, *a, **k: self.said.append(str(text))
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            self.stack.enter_context(patch.object(app, name, side_effect=speak))
        self.stack.enter_context(patch.object(app, 'interrupt_speech'))
        self.stack.enter_context(patch.object(app, 'finish_mail_announcement'))
        self.stack.enter_context(patch.object(app, 'WebSession', WebStandIn))
        self.stack.enter_context(patch('mail_voice.account_token', return_value=('me@example.com', 'token')))
        self.stack.enter_context(patch('google_contacts.sync', return_value='Google Contacts synced.'))
        self.stack.enter_context(patch('document_editor.copy_to_clipboard'))
        self.stack.enter_context(patch('webbrowser.open'))
        self.stack.enter_context(patch('subprocess.Popen'))
        self.stack.enter_context(patch('os.startfile', create=True))

    # Screens -------------------------------------------------------------
    def screen(self, mode):
        """Open a fresh copy of a screen with something on it, like a user would."""
        app.SCREEN_MENU = None; app.NAMED_LIST = None; app.pending_send = None; app.account_setup = False
        app.note_buffer[:] = []
        if mode == 'mailbox':
            from mail_voice import MailSession
            app.mail_session = MailSession('gmail', str(self.root), FakeMailClient)
            app.mail_session.process('open inbox')
        elif mode == 'email_draft':
            from email_draft import VoiceEmail
            from document_editor import Paragraph
            draft = VoiceEmail(self.root / 'Email Drafts', title='Hello', recipient='sam@example.com', subject='Hi', compose_step='body')
            draft.paragraphs = [Paragraph('Thank you.')]; draft.dictating = True
            app.email_draft = draft
        elif mode == 'note':
            app.OPEN_NOTE = None
            app.note_buffer[:] = ['Buy milk.']
        elif mode == 'web':
            app.web_session = WebStandIn(pending=None, submit_pending=None, list_focus=None, keyboard_field=None,
                                         form_index=None, choices=[], snapshot={'url': 'https://example.com', 'title': 'Example'})
        elif mode == 'help':
            app.help_return_mode = 'awake'
            app.help_session = app.HelpSession(app.GUIDE, release_notes_path=app.GUIDE.parent / 'Voice Companion Release Notes.txt')
        elif mode == 'tutorial':
            app.tutorial_session = None
            self.assertEqual(app.handle('start tutorial', 'awake'), 'tutorial')
        elif mode == 'file_reader':
            from document_reader import open_document
            from file_manager import FileManager
            from test_file_manager import FakeFiles, settle
            path = self.root / 'reader.txt'
            path.write_text('Title\n\nThe clinic opens at nine.\n\nThanks.', encoding='utf-8')
            app.FILE_MANAGER = FileManager(FakeFiles(self.root)); app.FILE_MANAGER.refresh(str(self.root)); settle(app.FILE_MANAGER)
            app.FILE_READER = open_document(path)
        self.said.clear()
        return mode

    def press(self, key, mode):
        request = app.navigation_key(key, mode)
        return app.handle_keyboard(request, mode) if request else mode

    def state(self, mode):
        if mode == 'note':
            return ' '.join(app.note_buffer)
        if mode == 'email_draft' and app.email_draft is not None:
            draft = app.email_draft
            return ' '.join([draft.recipient or '', draft.subject or '', ' '.join(draft.cc or []), ' '.join(draft.bcc or [])] + [p.text for p in draft.paragraphs])
        return ''

    # Tests ---------------------------------------------------------------
    def test_every_main_screen_answers_alt(self):
        for mode in ('mailbox', 'email_draft', 'note', 'web', 'help', 'tutorial', 'file_reader'):
            with self.subTest(mode=mode):
                self.screen(mode)
                self.assertIn(mode, MENUS)
                self.assertEqual(self.press('Alt', mode), mode)
                self.assertTrue(self.said and self.said[-1].startswith(MENUS[mode][0] + '. '), self.said)
                self.assertIn('1 of', self.said[-1])
                self.assertEqual(self.press('Alt', mode), mode)
                self.assertTrue(self.said[-1].startswith('Menu closed. '), self.said[-1])
        # The main menu is a menu already: Alt says so and where you are.
        self.said.clear()
        self.assertEqual(self.press('Alt', 'awake'), 'awake')
        self.assertEqual(self.said[-1], 'Main menu. File manager (F), 1 of 12.')
        self.assertEqual(self.press('Down', 'awake'), 'awake')
        self.assertIn('Word processor', self.said[-1])

    def test_word_processor_file_manager_and_player_keep_their_own_alt(self):
        self.assertEqual(app.navigation_key('Alt', 'document'), 'document menu')
        self.assertEqual(app.navigation_key('Alt', 'files'), 'file manager menu')
        self.assertIn(app.navigation_key('Alt', 'local_media'), ('@player_menu', '@player_close'))
        for mode in ('document', 'files', 'local_media', 'document_menu', 'word_processor'):
            self.assertNotIn(mode, ALT_SCREENS)
            self.assertIsNone(alt_menu_key({'mode': mode}))

    def test_every_menu_item_runs_a_command_its_screen_handles(self):
        for mode in MENUS:
            # What the screen says to words it does not know.
            self.screen(mode)
            before = self.state(mode)
            app.handle(NONSENSE, mode)
            unknown = list(self.said)
            for group, label, key, command in items(mode):
                with self.subTest(mode=mode, item=label):
                    self.screen(mode)
                    before = self.state(mode)
                    result = self.press('Alt', mode)
                    path = menu_path(mode, [g for g, _ in MENUS[mode][1]].index(group),
                                     [entry[0] for entry in dict(MENUS[mode][1])[group]].index(label))
                    for step in path.split(', ')[1:]:
                        result = self.press('Letter:' + step, result)
                    self.assertIsNone(app.SCREEN_MENU.mode if app.SCREEN_MENU else None, 'menu must close')
                    said = [text for text in self.said if not text.startswith((MENUS[mode][0], group + ' menu'))]
                    self.assertTrue(said, 'nothing was said')
                    if '{value}' in command:
                        self.assertEqual(result, 'task_input')
                        self.assertIn('Escape cancels', said[-1])
                        self.assertEqual(app.handle_keyboard('escape', 'task_input'), mode)
                        continue
                    self.assertNotEqual(said, unknown)
                    for text in said:
                        for fallback in FALLBACKS:
                            self.assertNotIn(fallback, text)
                    if mode in ('note', 'email_draft'):
                        # A command is never written into the note or message.
                        self.assertEqual(self.state(mode), before) if command not in ('new line', 'save note', 'main menu', 'discard draft', 'leave email', 'list notes') else None
                        self.assertNotIn(command.lower(), self.state(mode).lower())
                    if mode == 'web':
                        for call in app.web_session.command.call_args_list:
                            self.assertNotIn('@', str(call)); self.assertNotIn('{value}', str(call))
                    app.web_session = None; app.mail_session = None; app.email_draft = None
                    app.help_session = None; app.tutorial_session = None; app.NAMED_LIST = None

    def test_shortcut_keys_shown_in_menus_do_the_same_thing(self):
        # Internal names for keys the window sends on these screens.
        internal = {('mailbox', 'Page Down'): 'Next', ('mailbox', 'Page Up'): 'Prior',
                    ('web', 'Alt+Left'): 'Web:Back', ('web', 'Alt+Right'): 'Web:Forward', ('web', 'F5'): 'Web:Refresh',
                    ('web', 'Ctrl+L'): 'Web:Search'}
        expected = {('web', 'Escape'): 'web browser menu', ('file_reader', 'Escape'): 'back to files'}
        for mode in MENUS:
            self.screen(mode)
            for group, label, key, command in items(mode):
                if key.startswith('Alt, '):
                    continue
                with self.subTest(mode=mode, key=key):
                    request = app.navigation_key(internal.get((mode, key), key), mode)
                    if '{value}' in command:
                        self.assertEqual(request, '@prompt:' + command)
                    elif command.startswith('web key '):
                        self.assertEqual(request, command)
                    else:
                        self.assertEqual(request, expected.get((mode, key), command))

    def test_menu_keys_move_jump_go_back_and_close(self):
        mode = self.screen('mailbox')
        self.press('Alt', mode)
        self.assertEqual(self.said[-1], 'Email menu. Messages (M), 1 of 7.')
        self.press('Up', mode)
        self.assertEqual(self.said[-1], 'Start of list. Messages (M), 1 of 7.')
        self.press('End', mode)
        self.assertEqual(self.said[-1], 'Options and help (O), 7 of 7.')
        self.press('Letter:a', mode)
        self.assertEqual(self.said[-1], 'Accounts (A), 6 of 7.')
        self.press('Enter', mode)
        self.assertEqual(self.said[-1], 'Accounts menu. Add account (A), 1 of 3. Alt, 6, 1. Say add account.')
        self.press('Down', mode)
        self.assertEqual(self.said[-1], 'Switch account (S), 2 of 3. Alt, 6, 2. Say list email accounts.')
        self.press('Escape', mode)
        self.assertEqual(self.said[-1], 'Email menu. Accounts (A), 6 of 7.')
        self.press('Letter:1', mode)
        self.assertEqual(self.said[-1], 'Messages menu. Check for new mail (C), 1 of 9. F5. Say list messages.')
        self.press('Letter:r', mode)
        self.assertTrue(self.said[-1].startswith('Read message (R), 2 of 9. Enter.'))
        self.press('Letter:r', mode)
        self.assertTrue(self.said[-1].startswith('Reply (R), 3 of 9. Ctrl+R.'))
        self.press('Alt', mode)
        self.assertEqual(self.said[-1], 'Menu closed. Back to email.')
        self.assertIsNone(app.SCREEN_MENU.mode)
        # Escape from the group list closes the menu too.
        self.press('Alt', mode); self.press('Escape', mode)
        self.assertEqual(self.said[-1], 'Menu closed. Back to email.')
        # Another key closes the menu and does its own job.
        self.press('Alt', mode)
        self.assertEqual(self.press('Ctrl+I', mode), mode)
        self.assertIsNone(app.SCREEN_MENU.mode)
        self.assertIn('Lunch', self.said[-1])

    def test_voice_opens_and_uses_the_menu(self):
        mode = self.screen('mailbox')
        self.assertEqual(app.handle('Email menu.', mode), mode)
        self.assertTrue(self.said[-1].startswith('Email menu. '))
        app.handle('Next.', mode)
        self.assertTrue(self.said[-1].startswith('Select (S), 2 of 7.'))
        app.handle('That one.', mode)
        self.assertTrue(self.said[-1].startswith('Select menu. '))
        app.handle('Go back.', mode)
        self.assertEqual(self.said[-1], 'Email menu. Select (S), 2 of 7.')
        app.handle('Select.', mode)
        self.assertTrue(self.said[-1].startswith('Select menu. '))
        app.handle('Select all messages', mode)
        self.assertEqual(self.said[-1], '2 messages on this page selected.')
        self.assertIsNone(app.SCREEN_MENU.mode)
        # A command that is not in the menu closes it and works as usual.
        app.handle('menu', mode); app.handle('message details', mode)
        self.assertIsNone(app.SCREEN_MENU.mode)
        self.assertIn('Lunch', self.said[-1])

    def test_writing_words_never_open_a_menu(self):
        mode = self.screen('note')
        for words in ('Menu', 'Open menu', 'Help menu'):
            app.handle(words, mode)
            self.assertIsNone(app.SCREEN_MENU.mode if app.SCREEN_MENU else None)
        self.assertIn('Open menu', ' '.join(app.note_buffer))
        mode = self.screen('email_draft')
        app.handle('Menu', mode)
        self.assertIsNone(app.SCREEN_MENU.mode if app.SCREEN_MENU else None)

    def test_reader_main_menu_is_announced_once(self):
        mode = self.screen('file_reader')
        self.assertEqual(app.handle('Main menu.', mode), 'awake')
        self.assertEqual(sum(text.startswith('Main menu. What would you like') for text in self.said), 1, self.said)

    def test_menu_waits_for_a_question_or_picker(self):
        mode = self.screen('mailbox')
        app.mail_session.process('delete')
        self.assertTrue(app.mail_session.pending)
        self.assertEqual(self.press('Alt', mode), mode)
        self.assertIsNone(app.SCREEN_MENU.mode)

    def test_draft_menu_send_without_account_keeps_draft(self):
        mode = self.screen('email_draft')
        self.press('Alt', mode); self.press('Letter:1', mode)
        self.assertEqual(self.press('Letter:1', mode), 'email_draft')
        self.assertIn('Choose a connected email account first', ' '.join(self.said))
        self.assertIn('Thank you.', str(app.email_draft.paragraphs))

    def test_radio_alt_moves_between_actions_and_results(self):
        from media_navigation import MediaNavigation
        hub = MagicMock(browsing_episodes=False, preset_browsing=False, stations=[{'name': 'Jazz FM', 'url': 'http://x'}],
                        station_index=0, current_station=None, shows=[])
        hub.player.kind = None
        app.MEDIA_HUB = hub
        nav = MediaNavigation(hub, 'radio')
        with patch.object(app, 'media_navigation', return_value=nav):
            self.assertEqual(nav.focus, 'items')
            self.press('Alt', 'media')
            self.assertEqual(nav.focus, 'actions')
            self.assertTrue(self.said[-1].startswith('Radio menu. '), self.said[-1])
            self.press('Alt', 'media')
            self.assertEqual(nav.focus, 'items')
            self.assertTrue(self.said[-1].startswith('Menu closed. Results. Jazz FM'), self.said[-1])

    def test_alt_without_a_media_screen_does_nothing_else(self):
        app.web_session = WebStandIn(snapshot=None)
        self.assertEqual(app.handle('@sm:alt', 'media'), 'media')
        app.web_session.command.assert_not_called()

    def test_web_browser_menu_alt_repeats_the_menu(self):
        app.web_session = WebStandIn(snapshot=None)
        self.assertEqual(self.press('Alt', 'browser_menu'), 'browser_menu')
        app.web_session.menu.process.assert_called()
        self.assertEqual(app.web_session.menu.process.call_args.args[0], 'web browser menu')

    def test_alt_key_rules_for_the_window(self):
        self.assertEqual(alt_menu_key({'mode': 'mailbox'}), 'Alt')
        self.assertIsNone(alt_menu_key({'mode': 'mailbox'}, command_entry_active=True))
        self.assertIsNone(alt_menu_key({'mode': 'mailbox'}, typed_text='reply'))
        self.assertEqual(alt_menu_key({'mode': 'note'}, typed_text='Buy bread'), 'Alt')
        self.assertIsNone(alt_menu_key({'mode': 'mailbox', 'confirmation': 'yes'}))
        self.assertIsNone(alt_menu_key({'mode': 'web', 'web_field': {'tag': 'input'}}))
        self.assertIsNone(alt_menu_key({'mode': 'web', 'web_private': True}))
        self.assertIsNone(alt_menu_key({'mode': 'media', 'media_form': {'label': 'Search'}}))
        self.assertEqual(alt_menu_key({'mode': 'media', 'media_form': None}), 'Alt')
        self.assertEqual(alt_menu_key({'mode': 'web', 'screen_menu': True}), 'Alt')
        self.assertIsNone(alt_menu_key({'mode': 'settings'}))

    def test_open_menu_takes_arrows_letters_and_enter_from_the_window(self):
        self.assertEqual(overlay_key('Down', '', 0), 'Down')
        self.assertEqual(overlay_key('Return', '\r', 0), 'Enter')
        self.assertEqual(overlay_key('Tab', '\t', 1), 'ShiftTab')
        self.assertEqual(overlay_key('r', 'r', 0), 'Letter:r')
        self.assertEqual(overlay_key('3', '3', 0), 'Letter:3')
        self.assertEqual(overlay_key('space', ' ', 0), '')
        self.assertIsNone(overlay_key('o', 'o', 8))   # Alt+O still opens Options
        self.assertIsNone(overlay_key('r', '\x12', 4))   # Control+R still replies
        self.assertIsNone(overlay_key('F1', '', 0))
        self.assertIsNone(overlay_key('Alt_L', '', 0))

    def test_menus_are_small_enough_for_number_keys(self):
        for mode, (title, groups) in MENUS.items():
            self.assertLessEqual(len(groups), 9, mode)
            for name, entries in groups:
                self.assertLessEqual(len(entries), 9, (mode, name))
                labels = [label for label, _, _ in entries]
                self.assertEqual(len(labels), len(set(labels)), (mode, name))

    def test_guide_tells_how_to_open_each_menu(self):
        text = GUIDE.read_text(encoding='utf-8')
        html = GUIDE_HTML.read_text(encoding='utf-8')
        for mode, (title, groups) in MENUS.items():
            sentence = 'Press Alt to open the ' + title[0].lower() + title[1:] + '.'
            with self.subTest(mode=mode):
                self.assertIn(sentence, text)
                self.assertIn(sentence, html)
        for sentence in ('On the main menu, Alt says that you are in the main menu.',):
            self.assertIn(sentence, text)


@unittest.skipUnless(__import__('sys').platform == 'win32', 'Windows Tk keyboard integration')
class WindowAltKeyTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window = CompanionWindow('alt menu test'); self.window.start()
    def tearDown(self):
        if self.window.thread.is_alive(): self.call(lambda: self.window.root.destroy())
        self.window.thread.join(5)
    def call(self, function):
        import threading
        done = threading.Event(); errors = []
        def run():
            try: self.window.root.update_idletasks(); function()
            except Exception as exc: errors.append(exc)
            finally: done.set()
        self.window.ui_actions.put(('ui_call', run)); self.assertTrue(done.wait(5), self.state())
        if errors: raise errors[0]
    def state(self):
        # Shown when the window stops answering, so the build log says why.
        import sys, threading, traceback
        frame = sys._current_frames().get(self.window.thread.ident)
        others = [t.name for t in threading.enumerate() if t is not self.window.thread and t is not threading.current_thread()]
        return {'thread alive': self.window.thread.is_alive(), 'ready': self.window.ready.is_set(), 'problem': repr(self.window.problem),
                'waiting updates': self.window.ui_actions.qsize(), 'window errors': self.window.ui_errors,
                'window thread is at': ''.join(traceback.format_stack(frame)[-12:]) if frame else None,
                'other threads': others}
    def wait(self, condition):
        import time
        end = time.monotonic() + 5
        while not condition() and time.monotonic() < end: time.sleep(.015)
        self.assertTrue(condition(), self.state())
    def test_one_failed_update_does_not_stop_the_window(self):
        def broken(): raise ValueError('test failure inside the window')
        self.window.ui_actions.put(('ui_call', broken))
        self.window.set_context(dict(mode='mailbox', source=None, text='', caret=0, ack=0, focus=True))
        self.wait(lambda: self.window.context.get('mode') == 'mailbox')
        self.assertTrue(any('test failure inside the window' in error for error in self.window.ui_errors))
    def test_alt_alone_opens_and_alt_with_a_key_does_not(self):
        self.window.set_context(dict(mode='mailbox', source=None, text='', caret=0, ack=0, focus=True))
        self.wait(lambda: self.window.context.get('mode') == 'mailbox')
        typed = self.window.typed
        self.call(lambda: (typed.event_generate('<KeyPress>', keysym='Alt_L'), typed.event_generate('<KeyRelease>', keysym='Alt_L', state=8)))
        self.wait(lambda: ('keyboard', 'Alt') in self.window.commands.queue)
        self.window.commands.queue.clear()
        self.call(lambda: (typed.event_generate('<KeyPress>', keysym='Alt_L'), typed.event_generate('<KeyPress>', keysym='t', state=8),
                           typed.event_generate('<KeyRelease>', keysym='Alt_L', state=8)))
        import time; time.sleep(.2)
        self.assertNotIn(('keyboard', 'Alt'), self.window.commands.queue)
    def test_open_menu_takes_arrow_keys_from_the_message(self):
        self.window.set_context(dict(mode='mailbox', source=None, text='', caret=0, ack=0, focus=True, screen_menu=True))
        self.wait(lambda: self.window.context.get('screen_menu'))
        self.call(lambda: self.window.typed.event_generate('<KeyPress>', keysym='Down'))
        self.wait(lambda: ('keyboard', 'Down') in self.window.commands.queue)


if __name__ == '__main__':
    unittest.main()

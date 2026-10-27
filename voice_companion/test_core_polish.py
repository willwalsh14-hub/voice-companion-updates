"""Core fixes found in the October 2026 audit: sleep and wake, the microphone switch,
the main menu, updates and the menus' names. Each test failed before its fix."""
import queue
import unittest
from pathlib import Path
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class FakeWindow:
    def __init__(self, *commands):
        self.commands = queue.Queue()
        for command in commands:
            self.commands.put(command)
        self.spoken = []
        self.closed = mock.Mock(is_set=mock.Mock(return_value=False))

    def feedback(self, text):
        self.spoken.append(text)

    def set_context(self, context):
        pass

    def show(self, text):
        pass


class MicrophoneKeyWhileAsleepTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(MICROPHONE_OFF=True, MICROPHONE_FAILED=False, MICROPHONE_ANNOUNCED=False,
                                SLEEP_RETURN_MODE='awake').items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(companion.PREFERENCES); p.start(); self.addCleanup(p.stop)

    def test_f9_pressed_while_asleep_says_how_to_wake_and_stays_asleep(self):
        # The key arrives the real way: through the keyboard path, which works on the
        # screen behind sleep. It used to say "Voice commands are on." while still asleep.
        self.assertEqual(companion.handle_keyboard('@microphone', 'sleep'), 'sleep')
        self.assertFalse(companion.MICROPHONE_OFF)
        self.assertIn('Say wake up, or press Control+F12', self.said[-1])
        self.assertEqual(companion.SLEEP_RETURN_MODE, 'awake')

    def test_typed_microphone_on_while_asleep_says_how_to_wake(self):
        self.assertEqual(companion.handle_keyboard('microphone on', 'sleep'), 'sleep')
        self.assertIn('Say wake up', self.said[-1])


class MenuNamesTests(CompanionTestCase):
    """Will: "Some apps aren't saying menu, like Document reader menu." Every launch menu says
    its full name with the word menu, when it opens and when you come back to it."""
    def setUp(self):
        super().setUp()
        for name, value in dict(TASK_NAV=None, NAMED_LIST=None, FILE_READER=None, FILE_MANAGER=None, OPEN_NOTE=None,
                                PREFERENCES=dict(companion.PREFERENCES)).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion, 'note_buffer', []); p.start(); self.addCleanup(p.stop)

    def key(self, key, mode):
        request = companion.navigation_key(key, mode)
        return mode if request is None else companion.handle(request, mode)

    def test_each_launch_menu_says_its_full_name_and_menu_when_it_opens(self):
        for command, mode, title in (('document reader', 'reader_menu', 'Document reader menu. '),
                                     ('notepad', 'notepad_menu', 'Notepad menu. '),
                                     ('word processor', 'word_processor', 'Word processor menu. '),
                                     ('media', 'media_menu', 'Media menu. ')):
            with self.subTest(command=command):
                companion.TASK_NAV = None
                self.assertEqual(companion.handle(command, 'awake'), mode)
                self.assertTrue(self.said[-1].startswith(title), self.said[-1])

    def test_coming_back_to_a_launch_menu_says_menu(self):
        # Escape from the recent documents list of Document reader.
        nav = companion.task_navigation()
        nav.reader_recent = [str(self.folder / 'Orders.txt')]
        mode = nav.enter('reader_recent', 'Recent documents')
        self.assertEqual(self.key('Escape', mode), 'reader_menu')
        self.assertTrue(self.said[-1].startswith('Document reader menu. '), self.said[-1])
        # A file that is no longer there, opened from Document reader.
        # The reason and the menu are one message; a second message would stop the first.
        self.assertEqual(nav.read_file(self.folder / 'gone.txt'), 'reader_menu')
        self.assertTrue(self.said[-1].startswith('gone.txt is no longer there. It may have been moved or deleted. Document reader menu. '), self.said[-1])
        (self.folder / 'broken.docx').write_bytes(b'not a zip file')
        self.assertEqual(nav.read_file(self.folder / 'broken.docx'), 'reader_menu')
        self.assertIn('. Document reader menu. Open a document', self.said[-1])
        self.assertNotIn('..', self.said[-1])
        # Saving a note goes back to the Notepad menu.
        mode = companion.handle('notepad', 'awake')
        mode = self.key('Ctrl+N', mode)
        companion.handle('Buy milk.', mode)
        with mock.patch.object(companion, 'save_note', return_value=True):
            self.assertEqual(self.key('Escape', mode), 'notepad_menu')
        self.assertTrue(self.said[-1].startswith('Note saved. Notepad menu. '), self.said[-1])

    def test_canceling_a_typed_value_names_the_menu_it_returns_to(self):
        nav = companion.task_navigation()
        for target, phrase in (('word_processor', 'Canceled. Back to Word processor menu.'),
                               ('reader_menu', 'Canceled. Back to Document reader menu.'),
                               ('notepad_menu', 'Canceled. Back to Notepad menu.'),
                               ('local_media', 'Canceled. Back to Media player.'),
                               ('file_reader', 'Canceled. Back to document reader.')):
            nav.prompt = ('find {value}', target)
            self.assertEqual(companion.handle('cancel', 'task_input'), target)
            self.assertEqual(self.said[-1], phrase)

    def test_no_two_menus_share_a_name(self):
        # The Alt menu inside a note or a document being read used to have the same name
        # as the launch menu ("Notepad menu", "Document reader menu"), so you could not
        # tell which one you were in.
        from screen_menus import MENUS, OPEN_PHRASES
        launch = {'Word processor menu', 'Document reader menu', 'Notepad menu', 'Media menu', 'Media player menu',
                  'Web browser menu', 'Main menu', 'Options menu'}
        alt_titles = [title for title, _ in MENUS.values()]
        self.assertEqual(len(alt_titles), len(set(alt_titles)))
        self.assertFalse(launch & set(alt_titles), launch & set(alt_titles))
        self.assertEqual(MENUS['note'][0], 'Note menu')
        self.assertEqual(MENUS['file_reader'][0], 'Document menu')
        # The old names still open them, so nothing people learned stops working.
        self.assertIn('notepad menu', OPEN_PHRASES['note'])
        self.assertIn('document reader menu', OPEN_PHRASES['file_reader'])


class DialogWordTests(CompanionTestCase):
    """Will: dialogs say the word dialog when they open."""
    def test_ai_voice_setup_says_dialog(self):
        window = mock.Mock()
        with mock.patch.object(companion, 'APP_WINDOW', window):
            companion.handle('set up AI voice', 'awake')
        window.setup_ai_voice.assert_called_once()
        self.assertTrue(self.said[-1].startswith('AI voice setup dialog. '), self.said[-1])

    def test_options_announces_each_category_as_a_dialog(self):
        import inspect, native_settings
        source = inspect.getsource(native_settings.NativeSettings._run)
        self.assertIn("self.session.category_name()+' dialog. '", source)
        self.assertIn('last_focus[0]=focused_control()', source)


class MainMenuWhereAmITests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(MAIN_MENU_INDEX=None, MICROPHONE_OFF=False, MICROPHONE_FAILED=False).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def test_where_am_i_names_the_main_menu_and_the_item(self):
        # It used to give the general "I can write a note..." prompt.
        self.assertEqual(companion.handle('Where am I?', 'awake'), 'awake')
        self.assertEqual(self.said[-1], 'Main menu. The microphone is on.')
        companion.handle('next', 'awake')
        companion.MICROPHONE_OFF = True
        companion.handle('where am i', 'awake')
        self.assertEqual(self.said[-1], 'Main menu. File manager (F), 1 of 15. The microphone is off, as you left it. Press F9 to turn it on.')


class UnknownWordsOnLaunchMenusTests(CompanionTestCase):
    """A word the app does not understand used to throw you out to the main menu from
    Word processor, Notepad, Document reader, Media and the Media player, with a long
    unrelated prompt, and stopped a file playing in the player."""
    SCREENS = (('word processor', 'word_processor', 'Word processor menu'),
               ('notepad', 'notepad_menu', 'Notepad menu'),
               ('document reader', 'reader_menu', 'Document reader menu'),
               ('media', 'media_menu', 'Media menu'),
               ('media player', 'local_media', 'Media player'))

    def setUp(self):
        super().setUp()
        for name, value in dict(TASK_NAV=None, NAMED_LIST=None, FILE_READER=None, FILE_MANAGER=None).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def test_an_unknown_word_keeps_you_where_you_are(self):
        for command, mode, name in self.SCREENS:
            with self.subTest(screen=mode):
                companion.TASK_NAV = None
                self.assertEqual(companion.handle(command, 'awake'), mode)
                self.said.clear()
                self.assertEqual(companion.handle('banana bread', mode), mode)
                self.assertEqual(self.said, ['I did not understand that. You are on the ' + name + '. Press F1 for help.'])

    def test_main_menu_from_a_launch_menu_says_main_menu_once(self):
        for command, mode, name in self.SCREENS:
            with self.subTest(screen=mode):
                companion.TASK_NAV = None
                companion.handle(command, 'awake')
                self.said.clear()
                self.assertEqual(companion.handle('main menu', mode), 'awake')
                self.assertEqual(self.said, [companion.MAIN_MENU_PROMPT])

    def test_the_main_menu_still_explains_what_you_can_do(self):
        companion.handle('banana bread', 'awake')
        self.assertTrue(self.said[-1].startswith('I can write a note or document'), self.said)


class UpdaterStateTests(unittest.TestCase):
    def test_a_failed_download_or_handoff_leaves_the_updater_idle(self):
        import tempfile, threading, time
        from app_updates import AppUpdates
        with tempfile.TemporaryDirectory() as folder:
            updates = AppUpdates(folder, '0.2.170-test', folder)
            updates.release = {'version': '9.9.9', 'installer_url': 'https://example.invalid/s.exe', 'sha256': '0' * 64, 'size': 10}
            with mock.patch('app_updates.download_release', side_effect=OSError('offline')):
                self.assertTrue(updates.download())
                self.assertEqual(updates.events.get(timeout=5), ('download_failed', None))
            self.assertEqual(updates.phase, 'idle')
            with mock.patch.object(updates, 'launch', side_effect=RuntimeError('no handoff')):
                self.assertTrue(updates.begin_install(Path(folder) / 'x.exe', updates.release))
                self.assertEqual(updates.events.get(timeout=5), ('install_failed', None))
            self.assertEqual(updates.phase, 'idle')
            self.assertFalse(updates.busy)


class UpdateWhileInstallingTests(CompanionTestCase):
    def test_cancel_while_installing_does_not_promise_sleep_mode(self):
        service = mock.Mock(phase='preparing', percent=100)
        with mock.patch.object(companion, 'UPDATES', service):
            companion.handle('cancel update', 'update_download')
        self.assertEqual(self.said[-1], 'The update is being installed and cannot be canceled now. Voice Companion will close and reopen when it finishes.')


class HelpAndTutorialReturnTests(CompanionTestCase):
    """Leaving Help or the tutorial names where you are, in one message."""
    def setUp(self):
        super().setUp()
        for name, value in dict(TASK_NAV=None, NAMED_LIST=None, tutorial_session=None, MAIN_MENU_LEAD='').items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def key(self, key, mode):
        request = companion.navigation_key(key, mode)
        return mode if request is None else companion.handle(request, mode, typed=True)

    def test_closing_help_names_the_menu_you_return_to(self):
        mode = companion.handle('word processor', 'awake')
        mode = self.key('F1', mode)
        self.assertEqual(mode, 'help')
        self.assertNotIn('..', self.said[-1])
        mode = self.key('Escape', self.key('Escape', mode))
        self.assertEqual(mode, 'word_processor')
        self.assertEqual(self.said[-1], 'Help closed. Back to Word processor menu.')

    def test_closing_help_on_the_main_menu_is_one_message(self):
        mode = self.key('F1', 'awake')
        self.said.clear()
        self.assertEqual(self.key('Escape', mode), 'awake')
        self.assertEqual(self.said, ['Help closed. ' + companion.MAIN_MENU_PROMPT])

    def test_closing_the_tutorial_is_one_message(self):
        mode = companion.handle('start tutorial', 'awake')
        self.said.clear()
        self.assertEqual(self.key('Escape', mode), 'awake')
        self.assertEqual(self.said, ['Tutorial closed. ' + companion.MAIN_MENU_PROMPT])


class ExitKeepsWorkTests(CompanionTestCase):
    """Never lose user data: exit used to go ahead when the note could not be saved,
    and a draft that could not be saved stopped the app with an error."""
    def setUp(self):
        super().setUp()
        for name, value in dict(web_session=None, OPEN_NOTE=None, MEDIA_HUB=None, KEYBOARD_DIRTY={}).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion, 'note_buffer', ['Buy milk.']); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion, 'wait_for_speech', return_value=True); p.start(); self.addCleanup(p.stop)

    def test_exit_stays_open_when_the_note_cannot_be_saved(self):
        with mock.patch('notepad.add', side_effect=PermissionError('locked')):
            self.assertEqual(companion.finish_exit('note'), 'note')
        self.assertEqual(companion.note_buffer, ['Buy milk.'])
        self.assertIn('could not be saved', self.said[-1])

    def test_exit_stays_open_when_the_draft_cannot_be_saved(self):
        draft = self.draft()
        with mock.patch.object(draft, 'save', side_effect=PermissionError('locked')):
            self.assertEqual(companion.finish_exit('email_draft'), 'email_draft')
        self.assertEqual(self.said[-1], 'Your email draft could not be saved, so Voice Companion is still open. Close any program using your drafts folder, then try again.')

    def test_closing_the_window_saves_the_note_and_never_stops_with_an_error(self):
        with mock.patch('notepad.add') as add:
            companion.close_from_window('note')
        add.assert_called_once_with(self.folder, 'Buy milk.')
        self.assertEqual(self.said[-1], 'Voice Companion closed. Goodbye.')
        companion.note_buffer[:] = ['Second note.']
        editor = mock.Mock(); editor.save.side_effect = PermissionError('locked')
        companion.KEYBOARD_DIRTY[1] = (editor, 0)
        with mock.patch('notepad.add', side_effect=PermissionError('locked')):
            companion.close_from_window('note')     # no exception
        self.assertEqual(self.said[-1], 'Voice Companion closed. Some of your work could not be saved.')


class SpokenKeyNamesTests(CompanionTestCase):
    """Key names are spoken the way the guide writes them: "Control+O", never "Ctrl+O"."""
    def setUp(self):
        super().setUp()
        for name, value in dict(TASK_NAV=None, NAMED_LIST=None, FILE_MANAGER=None, SCREEN_MENU=None).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def test_spoken_key_uses_the_guide_words(self):
        from menu_navigation import spoken_key
        for key, words in (('Ctrl+O', 'Control+O'), ('Ctrl+Shift+S', 'Control+Shift+S'), ('Up', 'Up Arrow'),
                           ('Ctrl+Shift+Down', 'Control+Shift+Down Arrow'), ('PgDn', 'Page Down'), ('Esc', 'Escape'),
                           ('ShiftTab', 'Shift+Tab'), ('Alt, 3, 2', 'Alt, 3, 2'), ('F1', 'F1'), ('Enter', 'Enter'),
                           ('Insert+Shift+Down', 'Insert+Shift+Down Arrow'), ('Ctrl+Alt+1', 'Control+Alt+1'), ('', '')):
            self.assertEqual(spoken_key(key), words)

    def spoken_menu_text(self):
        """Everything a menu can say about its items, on every screen."""
        import file_manager, screen_menus, task_navigation, tutorial_curriculum
        texts = []
        nav = companion.task_navigation()
        for mode in ('word_processor', 'reader_menu', 'notepad_menu', 'media_menu', 'local_media'):
            items = nav.items(mode)
            texts += [nav.describe(item, i, len(items)) for i, item in enumerate(items)]
        for group in task_navigation.DOCUMENT_ACTIONS:
            nav.group = group
            items = nav.items('document_menu')
            texts += [nav.describe(item, i, len(items)) for i, item in enumerate(items)]
        menu = companion.screen_menu()
        for mode, (title, groups) in screen_menus.MENUS.items():
            menu.mode = mode
            for g, (_, entries) in enumerate(groups):
                menu.group = g
                if len(groups) > 1:
                    menu.level = 'items'
                texts += [menu.describe(i) for i in range(len(entries))]
        manager = file_manager.FileManager()
        for kind in (True, 'sort'):
            manager.menu = kind
            texts += manager.labels()
        texts.append(file_manager.HELP)
        texts += [text for course in tutorial_curriculum.__dict__.values() if isinstance(course, (list, tuple))
                  for entry in course if isinstance(entry, tuple) for text in entry if isinstance(text, str)]
        return texts

    def test_no_spoken_menu_text_says_ctrl(self):
        texts = self.spoken_menu_text()
        self.assertGreater(len(texts), 200)
        bad = [text for text in texts if 'Ctrl' in text or 'PgDn' in text or 'PgUp' in text]
        self.assertEqual(bad, [])


class OptionsLeavingTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        self.panel = mock.Mock(); self.panel.closed.is_set.return_value = False
        for name, value in dict(SETTINGS_PANEL=self.panel, SETTINGS_RETURN_MODE='word_processor', MAIN_MENU_LEAD='').items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def test_main_menu_in_options_closes_it_and_goes_to_the_main_menu(self):
        # It used to be passed to the dialog, which said "Setting not recognized."
        for request in ('main menu', 'Main menu.', '@main_menu'):
            self.panel.reset_mock(); self.said.clear()
            self.assertEqual(companion.handle(request, 'settings', typed=True), 'awake')
            self.panel.cancel.assert_called_once()
            self.panel.command.assert_not_called()
            self.assertEqual(self.said, ['Options closed. ' + companion.MAIN_MENU_PROMPT])

    def test_canceling_options_says_where_you_are(self):
        self.assertEqual(companion.options_closed('settings'), 'word_processor')
        self.assertEqual(self.said[-1], 'Options closed. Back to Word processor menu.')

    def test_the_options_window_passes_main_menu_and_microphone_keys_to_the_app(self):
        import inspect, native_settings
        source = inspect.getsource(native_settings.NativeSettings._run)
        self.assertIn("self.keyboard('Ctrl+F1')", source)
        self.assertIn("self.keyboard('F9')", source)


class MicrophoneRetryTests(CompanionTestCase):
    """The main loop retries a closed microphone every two seconds. A microphone that
    could not start after F9, or that was unplugged, used to fail silently."""
    def setUp(self):
        super().setUp()
        for name, value in dict(MICROPHONE_OFF=False, MICROPHONE_FAILED=False, MICROPHONE_ANNOUNCED=False).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def test_a_failed_open_is_said_once_and_recovery_is_said(self):
        companion.MICROPHONE_ANNOUNCED = True        # F9 just said "Microphone on."
        companion.microphone_retried(False, 'awake')
        self.assertEqual(self.said[-1], 'The microphone could not start, so voice commands are off for now. Use the keys. Voice commands start again by themselves when the microphone works.')
        companion.microphone_retried(False, 'awake')  # every two seconds: said only once
        self.assertEqual(len(self.said), 1)
        self.assertEqual(companion.microphone_state(), 'failed')
        companion.microphone_retried(True, 'awake')
        self.assertEqual(self.said[-1], 'The microphone is on.')
        self.assertEqual(companion.microphone_state(), 'on')

    def test_a_microphone_that_failed_at_startup_is_not_announced_again(self):
        companion.MICROPHONE_FAILED = True            # the startup announcement said so
        companion.microphone_retried(False, 'awake')
        self.assertEqual(self.said, [])

    def test_f9_on_then_open_is_not_said_twice(self):
        companion.MICROPHONE_ANNOUNCED = True
        companion.microphone_retried(True, 'awake')
        self.assertEqual(self.said, [])


class WakeNamesTests(CompanionTestCase):
    def test_waking_names_the_screen_the_same_way_as_everywhere_else(self):
        # It used to say "Returning to reader menu.", "local media", "file reader".
        for mode, name in (('reader_menu', 'Back to Document reader menu.'), ('word_processor', 'Back to Word processor menu.'),
                           ('local_media', 'Back to Media player.'), ('notepad_menu', 'Back to Notepad menu.'),
                           ('file_reader', 'Back to document reader.'), ('document', 'Back to document.')):
            with mock.patch.object(companion, 'SLEEP_RETURN_MODE', mode), mock.patch.object(companion, 'focus_email_entry'):
                self.assertEqual(companion.resume_from_sleep(), mode)
            self.assertEqual(self.said[-1], 'I am listening. ' + name)


if __name__ == '__main__':
    unittest.main()

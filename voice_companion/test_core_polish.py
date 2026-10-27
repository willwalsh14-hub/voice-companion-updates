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


if __name__ == '__main__':
    unittest.main()

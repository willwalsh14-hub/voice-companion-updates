"""Core fixes found in the October 2026 audit: sleep and wake, the microphone switch,
the main menu, updates and the menus' names. Each test failed before its fix."""
import queue
import unittest
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


if __name__ == '__main__':
    unittest.main()

"""App mode, sliders, closing the browser, and edit combo box suggestions on the web screen."""
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import companion as app
from app_window import show_context_text
from input_parity import screen_key
from test_web_assistant import FakeBackend
from web_assistant import WebSession
from web_browse import describe, voice_browse_key
from web_modes import mode_request, page_key, page_key_feedback, toggle_chord, window_request

from key_state import ALT_STATE

ALT = 0x20000  # Tk's Alt flag on Windows; on Windows 8 is Num Lock.


class SpokenModeTests(unittest.TestCase):
    def test_spoken_mode_changes_tolerate_speech_punctuation(self):
        for spoken, expected in [('App mode.', 'app'), ('app mode', 'app'), ('Application mode', 'app'),
                                 ('Focus mode.', 'app'), ('turn on app mode', 'app'), ('Virtual cursor off.', 'app'),
                                 ('turn off the virtual cursor', 'app'), ('Browse mode.', 'browse'),
                                 ('browse mode, please', 'browse'), ('Please browse mode.', 'browse'),
                                 ('virtual cursor on', 'browse'), ('Exit app mode', 'browse'), ('App mode off', 'browse'),
                                 ('toggle app mode', 'toggle'), ('Toggle virtual cursor.', 'toggle'),
                                 ('app store', None), ('open app mode settings', None), ('browse', None)]:
            with self.subTest(spoken=spoken):
                self.assertEqual(mode_request(spoken), expected)

    def test_next_slider_by_voice(self):
        self.assertEqual(voice_browse_key('Next slider.'), 'Quick:slider')
        self.assertEqual(voice_browse_key('previous slider'), 'Quick:Previous:slider')


class ModeKeyTests(unittest.TestCase):
    def test_insert_space_and_control_alt_space_switch_modes(self):
        self.assertTrue(toggle_chord('space', 0, insert_held=True))
        for alt in (ALT_STATE, ALT):
            self.assertTrue(toggle_chord('space', 4 | alt))
        self.assertFalse(toggle_chord('space', 0))
        self.assertFalse(toggle_chord('space', 4))
        self.assertFalse(toggle_chord('space', 4 | 1 | ALT))
        self.assertFalse(toggle_chord('k', 4 | ALT))

    def test_control_shift_space_switches_modes_too(self):
        # JAWS, NVDA and Narrator all keep Insert+Space for themselves.
        self.assertTrue(toggle_chord('space', 4 | 1))
        self.assertFalse(toggle_chord('space', 1))
        self.assertTrue(toggle_chord('space', 4 | ALT))  # Control+Alt+Space still works.
        # In app mode it is Companion's key, not the page's.
        self.assertIsNone(window_request('space', ' ', 4 | 1, {'web_app_mode': True}))

    def test_keys_are_named_the_way_the_browser_expects(self):
        for keysym, char, state, expected in [('k', 'k', 0, 'k'), ('K', 'K', 1, 'K'), ('j', 'j', 0, 'j'),
                                              ('Left', '', 0, 'ArrowLeft'), ('Left', '', 1, 'Shift+ArrowLeft'),
                                              ('ISO_Left_Tab', '', 1, 'Shift+Tab'), ('Tab', '\t', 0, 'Tab'),
                                              ('space', ' ', 0, 'Space'), ('Return', '\r', 0, 'Enter'),
                                              ('Escape', '\x1b', 0, 'Escape'), ('Prior', '', 0, 'PageUp'),
                                              ('k', '\x0b', 4, 'Control+KeyK'), ('Home', '', 4, 'Control+Home'),
                                              ('1', '1', 0, '1'), ('BackSpace', '\x08', 0, 'Backspace'),
                                              ('greater', '>', 1, '>'), ('Shift_L', '', 1, None)]:
            with self.subTest(keysym=keysym, state=state):
                self.assertEqual(page_key(keysym, char, state), expected)

    def test_app_mode_sends_page_keys_but_keeps_companion_keys(self):
        context = {'web_app_mode': True}
        self.assertEqual(window_request('k', 'k', 0, context), 'Web:Pass:k')
        self.assertEqual(window_request('Escape', '\x1b', 0, context), 'Web:Pass:Escape')
        self.assertEqual(window_request('Home', '', 4, context), 'Web:Pass:Control+Home')
        self.assertEqual(window_request('Left', '', 0, context), 'Web:Pass:ArrowLeft')
        for keysym, char, state in [('F5', '', 0), ('F6', '', 0), ('F7', '', 0), ('Left', '', ALT),
                                    ('Right', '', ALT_STATE), ('l', '\x0c', 4), ('Alt_L', '', 0)]:
            with self.subTest(keysym=keysym, state=state):
                self.assertIsNone(window_request(keysym, char, state, context))
        # Browse mode keeps every key for Companion.
        self.assertIsNone(window_request('k', 'k', 0, {}))

    def test_slider_takes_its_keys_and_letters_do_nothing(self):
        context = {'web_slider': True}
        for keysym, expected in [('Left', 'Web:Slider:Left'), ('Right', 'Web:Slider:Right'), ('Up', 'Web:Slider:Up'),
                                 ('Down', 'Web:Slider:Down'), ('Home', 'Web:Slider:Home'), ('End', 'Web:Slider:End'),
                                 ('Prior', 'Web:Slider:Prior'), ('Next', 'Web:Slider:Next'),
                                 ('Escape', 'Web:Escape'), ('Tab', 'Web:Tab'), ('Return', 'Web:Activate')]:
            with self.subTest(keysym=keysym):
                self.assertEqual(window_request(keysym, '', 0, context), expected)
        self.assertEqual(window_request('ISO_Left_Tab', '', 1, context), 'Web:ShiftTab')
        self.assertEqual(window_request('h', 'h', 0, context), '')
        self.assertIsNone(window_request('F5', '', 0, context))

    def test_close_browser_key_on_web_screens(self):
        for mode in ('web', 'browser_menu'):
            self.assertEqual(screen_key(mode, 'W', 5, {}), 'Ctrl+Shift+W')
            with patch.object(app, 'speak'):
                self.assertEqual(app.navigation_key('Ctrl+Shift+W', mode), 'close browser window')


class FeedbackTests(unittest.TestCase):
    def focus(self, **values):
        base = dict(id=1, label='Search', role='textbox', tag='input', type='text', value_text='', value_now=None,
                    checked=False, expanded=None, selected='', editable=True, password=False, value='', caret=0)
        base.update(values)
        return base

    def test_new_focus_value_changes_and_page_announcements(self):
        play = dict(id=2, label='Play (k)', role='button', tag='button', type='submit', editable=False)
        self.assertEqual(page_key_feedback('Tab', {'focus': self.focus()}, {'focus': play}), 'Play (k), button.')
        seek = dict(id=3, label='Seek slider', role='slider', tag='div', type='', value_text='1:23 of 4:05', editable=False)
        moved = dict(seek, value_text='1:28 of 4:05')
        self.assertEqual(page_key_feedback('ArrowRight', {'focus': seek}, {'focus': moved}), '1:28 of 4:05.')
        self.assertEqual(page_key_feedback('Tab', {'focus': None}, {'focus': seek}), 'Seek slider, 1:23 of 4:05.')
        self.assertEqual(page_key_feedback('k', {'focus': None}, {'focus': None, 'live': ['Paused', 'Paused']}), 'Paused.')
        self.assertEqual(page_key_feedback('k', {'focus': None}, {'focus': None}), '')

    def test_typing_in_a_page_box_echoes_characters_never_passwords(self):
        before = self.focus(value='ca', caret=2)
        self.assertEqual(page_key_feedback('t', {'focus': before}, {'focus': self.focus(value='cat', caret=3)}), 't')
        self.assertEqual(page_key_feedback('Backspace', {'focus': before}, {'focus': self.focus(value='c', caret=1)}), 'a')
        self.assertEqual(page_key_feedback('ArrowLeft', {'focus': before}, {'focus': self.focus(value='ca', caret=1)}), 'a')
        secret = self.focus(type='password', password=True, value='', caret=None)
        self.assertEqual(page_key_feedback('x', {'focus': secret}, {'focus': dict(secret)}), '')


class SliderDescriptionTests(unittest.TestCase):
    def test_youtube_seek_slider_is_named_once(self):
        self.assertEqual(describe({'role': 'slider', 'label': 'Seek slider', 'value_text': '1:23 of 4:05'}), 'Seek slider, 1:23 of 4:05.')
        self.assertEqual(describe({'role': 'slider', 'label': 'Volume', 'value_text': '100% volume'}), 'Volume, slider, 100% volume.')
        self.assertEqual(describe({'role': 'slider', 'label': 'Volume', 'value_now': '25'}), 'Volume, slider, 25.')


class EditorRaceTests(unittest.TestCase):
    def test_letters_typed_in_a_web_box_are_not_undone_by_an_older_context(self):
        """Typing c, a, t fast: the app's answer for c must not erase a and t.

        Erasing them made the next letter conflict, and Companion said the text changed."""
        old = {'mode': 'web', 'source': 'web:1:field:main:n4:document:1', 'web_field': {'key': 'n4'}, 'readonly': False}
        older = dict(old, text='c', ack=1, focus=True)
        self.assertFalse(show_context_text(old, older, seq=3))
        self.assertTrue(show_context_text(old, dict(older, ack=3), seq=3))
        # A new field, a reading cursor, or a document still take the app's text.
        self.assertTrue(show_context_text(old, dict(older, source='web:1:page'), seq=3))
        self.assertTrue(show_context_text({'mode': 'web', 'source': 's'}, {'mode': 'web', 'source': 's', 'readonly': True, 'focus': True, 'ack': 0}, seq=3))
        self.assertTrue(show_context_text({'mode': 'document', 'source': 'd'}, {'mode': 'document', 'source': 'd', 'focus': True, 'ack': 0}, seq=3))


class AppModeWindowTests(unittest.TestCase):
    """Each page key in app mode returns at once: the window is not refocused
    or given the whole page again when nothing on it changed."""
    def setUp(self):
        import queue
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend();self.backend.page = object()
        self.backend.press_page_key = lambda key, focus=None, **kw: ({'focus': None}, {'focus': None})
        self.session = WebSession(self.temp.name, self.backend);self.session._refresh()
        self.window = Mock();self.window.commands = queue.Queue()
        for name in ('APP_WINDOW', 'UI_CONTEXT_CACHE', 'speak', 'KEYBOARD_SPEECH'):
            value = {'APP_WINDOW': self.window, 'UI_CONTEXT_CACHE': None, 'speak': Mock(), 'KEYBOARD_SPEECH': None}[name]
            patcher = patch.object(app, name, value);patcher.start();self.addCleanup(patcher.stop)
        patcher = patch.object(app, 'web_session', self.session);patcher.start();self.addCleanup(patcher.stop)

    def run_keys(self, *keys):
        self.window.set_context.reset_mock();self.window.wait_for_context.reset_mock()
        for key in keys:self.window.commands.put(('keyboard', key))
        return app.process_keyboard_batch('web')

    def test_page_keys_do_not_refocus_the_window(self):
        self.run_keys('Web:ToggleAppMode')
        self.assertTrue(self.session.app_mode)
        self.window.set_context.assert_called()  # The window learns about app mode.
        self.run_keys('Web:Pass:k', 'Web:Pass:j')
        self.window.set_context.assert_not_called()
        self.window.wait_for_context.assert_not_called()

    def test_queued_page_keys_are_sent_together_and_read_once(self):
        self.run_keys('Web:ToggleAppMode')
        calls = []
        def press(key, focus=None, before=None, read_after=True):
            calls.append((key, read_after)); return {'focus': None}, ({'focus': None} if read_after else None)
        self.backend.press_page_key = press
        self.run_keys('Web:Pass:ArrowRight', 'Web:Pass:ArrowRight', 'Web:Pass:ArrowRight', 'Web:Pass:k')
        self.assertEqual(calls, [('ArrowRight', False), ('ArrowRight', False), ('ArrowRight', False), ('k', True)])

    def test_other_keys_and_a_new_page_still_refocus(self):
        self.run_keys('Web:ToggleAppMode')
        self.run_keys('Web:ToggleAppMode')
        self.assertFalse(self.session.app_mode)
        self.assertTrue(self.window.set_context.call_args.args[0]['focus'])
        self.run_keys('Web:ToggleAppMode')
        # A key that opened another page or tab: the window takes focus back.
        self.backend.press_page_key = lambda key, focus=None, **kw: (setattr(self.backend, 'page', object()), ({'focus': None}, {'focus': None}))[1]
        self.run_keys('Web:Pass:Enter')
        self.assertTrue(self.window.set_context.call_args.args[0]['focus'])
        self.window.wait_for_context.assert_called_once()


class ScreenReaderNoticeTests(unittest.TestCase):
    """With a screen reader running, the first web page says once which key switches modes."""
    def setUp(self):
        import web_modes
        self.web_modes = web_modes
        patcher = patch.object(web_modes, 'NOTICE_STATE', {'given': False, 'checked_at': -1e9, 'readers': ()})
        patcher.start();self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def test_screen_readers_are_found_by_their_programs(self):
        found = self.web_modes.screen_readers
        self.assertEqual(found({'explorer.exe', 'jfw.exe'}), ['JAWS'])
        self.assertEqual(found({'NVDA.exe', 'nvda_slave.exe'}), ['NVDA'])
        self.assertEqual(found({'Narrator.exe'}), ['Narrator'])
        self.assertEqual(found({'jfw.exe', 'narrator.exe'}), ['JAWS', 'Narrator'])
        self.assertEqual(found({'chrome.exe'}), [])

    def test_the_notice_names_the_screen_reader_and_the_key(self):
        notice = self.web_modes.screen_reader_notice
        self.assertEqual(notice(['JAWS']), 'JAWS is running, and it uses Insert+Space for itself. '
                         'To switch between browse mode and app mode, press Control+Shift+Space.')
        self.assertTrue(notice(['NVDA', 'Narrator']).startswith('NVDA and Narrator are running, and they use Insert+Space for themselves.'))
        self.assertEqual(notice([]), '')

    def test_said_once_with_the_first_page_while_one_runs(self):
        with patch.object(self.web_modes, 'running_programs', return_value={'jfw.exe'}) as programs:
            session = WebSession(self.temp.name, FakeBackend())
            first = session._refresh(read=False)
            self.assertTrue(first.startswith('JAWS is running'), first)
            self.assertIn('Page loaded', first)
            self.assertNotIn('JAWS', session._refresh(read=False))
            self.assertNotIn('JAWS', WebSession(self.temp.name, FakeBackend())._refresh(read=False))
        self.assertEqual(programs.call_count, 1)

    def test_nothing_is_said_without_a_screen_reader_and_the_check_is_not_repeated_at_once(self):
        with patch.object(self.web_modes, 'running_programs', return_value={'explorer.exe'}) as programs:
            session = WebSession(self.temp.name, FakeBackend())
            self.assertTrue(session._refresh(read=False).startswith('Page loaded'))
            session._refresh(read=False)
        self.assertEqual(programs.call_count, 1)

    def test_program_list_never_fails(self):
        self.assertIsInstance(self.web_modes.running_programs(), set)


class ComboBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.controls = [{'key': 'q', 'role': 'combobox', 'label': 'Search', 'type': 'text', 'tag': 'input', 'value': '', 'combo_edit': True}]
        self.options = []
        self.typed = []
    def type_combo(self, item, value):
        self.typed.append(value)
        return True
    def combo_state(self, item, typed=None, wait=False):
        return {'options': list(self.options), 'active': None, 'value': item.get('value', ''), 'expanded': bool(self.options)}


class SuggestionNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.backend = ComboBackend();self.session = WebSession(self.temp.name, self.backend);self.session._refresh()
        self.session.browse.index = 0;self.session.focus_current()

    def type(self, text):
        self.session.keyboard_field['value'] = text
        self.assertEqual(self.session.combo_typed(), '')

    def test_new_suggestions_are_announced_once_after_typing_pauses(self):
        self.type('c');self.backend.options = ['cats', 'cat videos']
        now = time.monotonic()
        self.assertEqual(self.session.suggestions_notice(now), '')  # Still typing.
        self.assertEqual(self.session.suggestions_notice(now + 1), 'New suggestions available.')
        self.assertEqual(self.session.suggestions_notice(now + 2), '')  # Same list: once only.
        self.type('ca');self.assertEqual(self.session.suggestions_notice(time.monotonic() + 1), '')  # The list did not change.
        self.type('cat v');self.backend.options = ['cat videos']
        self.assertEqual(self.session.suggestions_notice(time.monotonic() + 1), 'New suggestions available.')
        self.type('cat vx');self.backend.options = []
        self.assertEqual(self.session.suggestions_notice(time.monotonic() + 1), '')  # No list: nothing to say.
        self.session.keyboard_field = None
        self.assertEqual(self.session.suggestions_notice(time.monotonic() + 1), '')


class CloseBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend();self.backend.close = Mock()
        self.session = WebSession(self.temp.name, self.backend);self.session._refresh()

    def test_close_browser_window_closes_even_when_kept_open(self):
        for phrase, mode in [('Close browser window.', 'web'), ('close the browser', 'web'), ('close browser window', 'browser_menu'),
                             ('Close browser.', 'awake')]:
            with self.subTest(phrase=phrase, mode=mode):
                self.session._refresh();self.backend.close.reset_mock()
                with patch.object(app, 'web_session', self.session), patch.object(app, 'PREFERENCES', {'web_keep_open': True}), \
                        patch.object(app, 'speak') as speak, patch.object(app, 'speak_prompt'), patch.object(app, 'APP_WINDOW', None), \
                        patch.object(app, 'pending_website', None):
                    self.assertEqual(app.handle(phrase, mode), 'awake')
                self.backend.close.assert_called_once()
                self.assertIsNone(self.session.snapshot)
                self.assertIn('Browser window closed.', [call.args[0] for call in speak.call_args_list])

    def test_leaving_website_still_keeps_the_page_when_asked(self):
        with patch.object(app, 'web_session', self.session), patch.object(app, 'PREFERENCES', {'web_keep_open': True}), \
                patch.object(app, 'speak'), patch.object(app, 'speak_prompt'), patch.object(app, 'APP_WINDOW', None), \
                patch.object(app, 'pending_website', None):
            self.assertEqual(app.handle('leave website', 'web'), 'browser_menu')
        self.backend.close.assert_not_called();self.assertIsNotNone(self.session.snapshot)

    def test_regular_browser_tabs_stay_open(self):
        self.backend.regular = True
        with patch.object(app, 'web_session', self.session), patch.object(app, 'PREFERENCES', {'web_keep_open': True}), \
                patch.object(app, 'speak') as speak, patch.object(app, 'speak_prompt'), patch.object(app, 'APP_WINDOW', None), \
                patch.object(app, 'pending_website', None):
            self.assertEqual(app.handle('close browser window', 'web'), 'awake')
        self.assertIn('Companion let go of your browser. Its tabs stay open.', [call.args[0] for call in speak.call_args_list])


if __name__ == '__main__':
    unittest.main()

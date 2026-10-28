"""Windows checks for the time and date keys in the real window and Options dialog.

F12, or Insert+F12, sends one request; a second quick press sends the date
request. Control+F12 is microphone sleep. On Windows, Tk reports Num Lock with
state bit 8, so F12 must work with that bit set.
"""
from real_window_tests import real_window
import sys
import threading
import time
import unittest
from datetime import datetime


@real_window
class WindowClockKeyTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window = CompanionWindow('clock key test')
        self.window.start()

    def tearDown(self):
        if self.window.thread.is_alive():
            self.call(lambda: self.window.root.destroy())
        self.window.thread.join(5)

    def call(self, function):
        done = threading.Event()
        errors = []

        def run():
            try:
                self.window.root.update_idletasks()
                function()
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        self.window.ui_actions.put(('ui_call', run))
        self.assertTrue(done.wait(5))
        if errors:
            raise errors[0]

    def wait(self, condition):
        end = time.monotonic() + 5
        while not condition() and time.monotonic() < end:
            time.sleep(.015)
        self.assertTrue(condition())

    def screen(self, **context):
        while not self.window.commands.empty():
            self.window.commands.get_nowait()
        self.window.set_context(dict(dict(source=None, text='', caret=0, ack=0, focus=True), **context))
        self.wait(lambda: self.window.context.get('mode') == context['mode'])

    def press(self, keysym, state=0, widget=None):
        target = widget or self.window.typed

        def send():
            target.focus_force()  # Synthetic keys go to the focused widget.
            target.event_generate('<KeyPress>', keysym=keysym, state=state)
        self.call(send)

    def requests(self):
        return [item for item in list(self.window.commands.queue) if isinstance(item, tuple) and item[0] == 'keyboard']

    def test_f12_once_and_twice_on_each_screen(self):
        # In an open document F12 alone is Save As (Will, October 2026); see test_insert_f12_and_num_lock.
        for context in (dict(mode='awake'), dict(mode='note'), dict(mode='mailbox'), dict(mode='files'),
                        dict(mode='document_menu'), dict(mode='email_draft', source='email:1'),
                        dict(mode='web', web_browse=True), dict(mode='web', web_app_mode=True),
                        dict(mode='awake', screen_menu=True), dict(mode='awake', confirmation='yes'),
                        dict(mode='web', web_private=True, web_field={'label': 'Password'})):
            with self.subTest(context=context):
                self.screen(**context)
                time.sleep(0.6)  # A new first press.
                self.press('F12')
                self.press('F12')
                self.wait(lambda: len(self.requests()) >= 2)
                self.assertEqual(self.requests()[:2], [('keyboard', 'F12'), ('keyboard', 'Double:F12')])
                self.assertFalse(any('Pass' in str(item) for item in self.window.commands.queue))

    def test_slow_presses_each_say_the_time(self):
        self.screen(mode='awake')
        time.sleep(0.6)
        self.press('F12')
        time.sleep(0.7)
        self.press('F12')
        self.wait(lambda: len(self.requests()) >= 2)
        self.assertEqual(self.requests()[:2], [('keyboard', 'F12'), ('keyboard', 'F12')])

    def test_insert_f12_and_num_lock(self):
        self.screen(mode='document', source='document:1')
        time.sleep(0.6)
        self.press('Insert')
        self.press('F12')
        self.call(lambda: self.window.typed.event_generate('<KeyRelease>', keysym='Insert'))
        self.wait(lambda: ('keyboard', 'F12') in self.requests())
        time.sleep(0.6)
        while not self.window.commands.empty():
            self.window.commands.get_nowait()
        # F12 alone in a document is Save As, with Num Lock on too.
        self.press('F12', state=8)  # Num Lock on.
        self.wait(lambda: ('keyboard', 'Doc:SaveAs') in self.requests())
        self.assertNotIn(('keyboard', 'F12'), self.requests())

    def test_control_f12_is_microphone_sleep(self):
        self.screen(mode='awake')
        self.press('F12', state=4)
        self.wait(lambda: ('keyboard', 'Ctrl+F12') in self.requests())
        self.assertNotIn(('keyboard', 'F12'), self.requests())


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows controls require Windows')
class OptionsClockKeyTests(unittest.TestCase):
    def test_f12_in_the_options_window(self):
        import ctypes as c
        from ctypes import wintypes as w
        from native_settings import NativeSettings
        from settings_model import SettingsSession, DEFAULTS
        keys = []
        context = {'engines': ('windows',), 'windows_voices': ('Test voice',), 'espeak_voices': (), 'ai_voices': ('coral',),
                   'accounts': (), 'podcasts': (), 'podcast_limits': {}}
        values = DEFAULTS | {'rate': '0', 'volume': '100', 'punctuation': 'some', 'input_mode': 'mixed', 'engine': 'windows',
                             'windows_voice': 'Test voice', 'ai_voice': 'coral', 'email_list_size': '10', 'browser': 'firefox',
                             'radio_source': 'all', 'podcast_limit': 'manual', 'ai_consent': False, 'remove_ai': False, 'api_key': ''}
        panel = NativeSettings(SettingsSession(values, context), lambda text: None, lambda values: None, lambda key: None,
                               keyboard=keys.append).start()
        try:
            self.assertTrue(panel.ready.wait(8))
            u = c.WinDLL('user32', use_last_error=True)
            u.GetDlgItem.restype = w.HWND
            u.GetDlgItem.argtypes = [w.HWND, c.c_int]
            u.PostMessageW.restype = w.BOOL
            u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
            categories = u.GetDlgItem(panel.hwnd, 100)
            u.PostMessageW(categories, 0x100, 0x7B, 0)
            u.PostMessageW(categories, 0x100, 0x7B, 0)
            end = time.monotonic() + 5
            while len(keys) < 2 and time.monotonic() < end:
                time.sleep(.02)
            self.assertEqual(keys, ['F12', 'Double:F12'])
        finally:
            panel.cancel()
            panel.closed.wait(3)


@unittest.skipUnless(sys.platform == 'win32', 'Windows region settings')
class RegionalDateTests(unittest.TestCase):
    def test_windows_long_date_names_the_day_and_year(self):
        import time_date
        text = time_date.regional_long_date(datetime(2026, 10, 22))
        self.assertTrue(text)
        self.assertIn('2026', text)
        self.assertIn('22', text)
        self.assertEqual(time_date.spoken_date(datetime(2026, 10, 22)), text)
        # The runner uses English (United States).
        self.assertIn('Thursday', text)


if __name__ == '__main__':
    unittest.main()

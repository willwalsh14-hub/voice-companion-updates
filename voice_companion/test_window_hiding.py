"""Thunderbird's window off the taskbar ("Hidden from the taskbar"), and every window it opens shown.

Will, October 2026: Thunderbird should run quietly, with no window in the way,
but its sign-in and other windows must always reach the user. The desktop is a
fake that follows Windows' own rules: a window is on the taskbar and in Alt+Tab
when it is visible, unowned, and has no tool-window style; and the taskbar only
notices a style change when the window is hidden and shown again.
"""
import unittest

import window_hiding as hiding
from window_hiding import ThunderbirdWindows, Window

TOOL, APP = 0x80, 0x40000


class FakeDesktop:
    def __init__(self):
        self.items = {}
        self.next = 100
        self.foreground = None
        self.allow_foreground = True
        self.calls = []

    def open(self, kind=hiding.MAIN_CLASS, title='Inbox - Mozilla Thunderbird', minimized=False, owned=False, style=0x100):
        handle = self.next; self.next += 1
        self.items[handle] = {'kind': kind, 'title': title, 'minimized': minimized, 'owned': owned, 'style': style,
                              'visible': True, 'button': not owned and not style & TOOL}
        return handle

    def close(self, handle):
        self.items.pop(handle)

    def taskbar(self, handle):
        return self.items[handle]['button']

    def windows(self):
        return [Window(h, w['kind'], w['title'], w['minimized'], w['owned'], bool(w['style'] & TOOL))
                for h, w in self.items.items() if w['visible']]

    def exists(self, handle):
        return handle in self.items

    def style(self, handle):
        return self.items[handle]['style']

    def _show(self, handle, minimized):
        item = self.items[handle]
        item['visible'] = True; item['minimized'] = minimized
        # Windows decides the taskbar button when the window is shown.
        item['button'] = not item['owned'] and (not item['style'] & TOOL or bool(item['style'] & APP))

    def tuck(self, handle):
        self.calls.append(('tuck', handle))
        item = self.items[handle]; before = item['style']
        item['visible'] = False
        item['style'] = (before | TOOL) & ~APP
        self._show(handle, True)
        return before

    def untuck(self, handle, style=None):
        self.calls.append(('untuck', handle))
        if handle not in self.items: return
        item = self.items[handle]
        item['visible'] = False
        item['style'] = style if style is not None else item['style'] & ~TOOL
        self._show(handle, item['minimized'])

    def minimize(self, handle):
        self.calls.append(('minimize', handle))
        self.items[handle]['minimized'] = True

    def restore(self, handle):
        """Thunderbird bringing its own window up."""
        self.items[handle]['minimized'] = False

    def bring_forward(self, handle):
        self.calls.append(('front', handle))
        self.items[handle]['minimized'] = False
        if self.allow_foreground: self.foreground = handle
        return self.foreground == handle


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


class HiddenThunderbirdTests(unittest.TestCase):
    def setUp(self):
        self.desktop = FakeDesktop(); self.clock = Clock(); self.signing_in = False
        self.main = self.desktop.open(minimized=True)
        self.windows = ThunderbirdWindows(self.desktop, busy=lambda: self.signing_in, clock=self.clock)

    def wait(self, seconds, step=0.5):
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.clock.now += step; self.windows.poll()

    def test_minimized_main_window_leaves_the_taskbar_and_comes_back(self):
        self.windows.enable()
        self.assertFalse(self.desktop.taskbar(self.main))
        self.assertTrue(self.desktop.items[self.main]['visible'])      # still running, still a window
        self.assertTrue(self.desktop.items[self.main]['minimized'])
        self.windows.disable()
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertEqual(self.desktop.items[self.main]['style'], 0x100)

    def test_sign_in_window_comes_to_the_front_at_once_with_taskbar_back(self):
        self.windows.enable()
        sign_in = self.desktop.open(hiding.DIALOG_CLASS, 'Sign in - Google Accounts', owned=True)
        self.clock.now += 0.5; self.windows.poll()
        self.assertEqual(self.desktop.foreground, sign_in)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertEqual(self.windows.take_message(), hiding.SIGN_IN)
        # It stays: the main window is not hidden again while the sign-in is open.
        self.wait(10)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertIsNone(self.windows.take_message())

    def test_hidden_again_only_after_the_window_closes_and_things_stay_quiet(self):
        self.windows.enable()
        prompt = self.desktop.open(hiding.DIALOG_CLASS, 'Mail Server Password Required', owned=True)
        self.wait(0.5)
        self.assertEqual(self.windows.take_message(), hiding.PASSWORD)
        self.desktop.close(prompt)
        self.wait(1)
        self.assertTrue(self.desktop.taskbar(self.main))   # restored by bring_forward, still on screen
        self.desktop.minimize(self.main)
        self.wait(1)
        self.assertTrue(self.desktop.taskbar(self.main))   # not yet: a few quiet seconds first
        self.wait(3)
        self.assertFalse(self.desktop.taskbar(self.main))
        self.assertIsNone(self.windows.take_message())

    def test_writing_window_the_add_on_minimizes_is_neither_announced_nor_hidden(self):
        self.windows.enable()
        compose = self.desktop.open(title='Write: (no subject) - Thunderbird')
        self.wait(1.5)
        self.desktop.minimize(compose)
        self.wait(10)
        self.assertIsNone(self.windows.take_message())
        self.assertTrue(self.desktop.taskbar(compose))     # Thunderbird opened it: never hidden
        self.assertFalse(self.desktop.taskbar(self.main))

    def test_any_new_window_that_stays_on_screen_is_brought_forward(self):
        self.windows.enable()
        other = self.desktop.open(title='Something new')
        self.wait(2.5)
        self.assertIsNone(self.desktop.foreground)          # it may still be a writing window about to minimize
        self.wait(1)
        self.assertEqual(self.desktop.foreground, other)
        self.assertEqual(self.windows.take_message(), hiding.OTHER.format(title=': Something new'))
        self.assertTrue(self.desktop.taskbar(self.main))

    def test_thunderbird_restoring_its_own_hidden_window_brings_it_forward(self):
        self.windows.enable()
        self.desktop.restore(self.main)
        self.wait(0.5)
        self.assertEqual(self.desktop.foreground, self.main)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertEqual(self.windows.take_message(), hiding.MAIN_UP)

    def test_a_window_that_cannot_take_the_front_says_how_to_find_it(self):
        self.desktop.allow_foreground = False
        self.windows.enable()
        self.desktop.open(hiding.DIALOG_CLASS, 'Security Exception', owned=True)
        self.wait(0.5)
        self.assertEqual(self.windows.take_message(), hiding.CERTIFICATE + hiding.NOT_IN_FRONT)

    def test_signing_in_keeps_everything_as_before_until_it_is_done(self):
        self.windows.enable()
        self.signing_in = True
        self.wait(0.5)
        self.assertTrue(self.desktop.taskbar(self.main))
        # Windows a provider opens during sign-in are left exactly as Thunderbird made them.
        extra = self.desktop.open(hiding.DIALOG_CLASS, 'Verify it is you', owned=True)
        self.wait(5)
        self.assertEqual(self.desktop.calls.count(('front', extra)), 0)
        self.assertNotIn(('tuck', self.main), self.desktop.calls[1:])
        self.assertIsNone(self.windows.take_message())
        self.desktop.close(extra)
        self.signing_in = False
        self.wait(1)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.wait(3)
        self.assertFalse(self.desktop.taskbar(self.main))

    def test_main_window_already_showing_is_left_until_minimized(self):
        self.desktop.restore(self.main)
        self.windows.enable()
        self.wait(5)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertIsNone(self.windows.take_message())
        self.desktop.minimize(self.main)
        self.wait(4)
        self.assertFalse(self.desktop.taskbar(self.main))

    def test_show_and_hide_commands(self):
        self.windows.enable()
        self.assertEqual(self.windows.show(), hiding.SHOWN)
        self.assertEqual(self.desktop.foreground, self.main)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.wait(10)
        self.assertTrue(self.desktop.taskbar(self.main))   # shown on purpose: stays shown
        self.assertEqual(self.windows.hide(), hiding.HIDDEN_NOW)
        self.assertTrue(self.desktop.items[self.main]['minimized'])
        self.assertFalse(self.desktop.taskbar(self.main))

    def test_hide_keeps_a_window_that_may_need_the_user(self):
        self.windows.enable()
        self.desktop.open(hiding.DIALOG_CLASS, 'Send Unsent Messages', owned=True)
        self.wait(0.5)
        self.assertEqual(self.windows.take_message(), hiding.UNSENT)
        self.assertEqual(self.windows.hide(), hiding.STILL_NEEDED)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.signing_in = True
        self.assertEqual(self.windows.hide(), hiding.SIGNING_IN)

    def test_hide_without_the_option_only_minimizes(self):
        self.desktop.restore(self.main)
        self.assertEqual(self.windows.hide(), hiding.MINIMIZED_NOW)
        self.assertTrue(self.desktop.items[self.main]['minimized'])
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertNotIn(('tuck', self.main), self.desktop.calls)

    def test_hide_on_a_thunderbird_the_user_opened_only_minimizes(self):
        self.windows.enable(); self.windows.show()
        self.assertEqual(self.windows.hide(started_by_companion=False), hiding.MINIMIZED_NOW)
        self.assertTrue(self.desktop.taskbar(self.main))

    def test_nothing_running(self):
        self.desktop.close(self.main)
        self.assertEqual(self.windows.show(), hiding.NOT_RUNNING)
        self.assertEqual(self.windows.hide(), hiding.NOT_RUNNING)

    def test_closed_hidden_window_is_forgotten(self):
        self.windows.enable()
        self.desktop.close(self.main)
        self.wait(1)
        self.assertEqual(self.windows.tucked, {})
        self.windows.disable()

    def test_option_off_does_nothing_at_all(self):
        self.desktop.open(hiding.DIALOG_CLASS, 'Sign in', owned=True)
        self.wait(5)
        self.assertEqual(self.desktop.calls, [])
        self.assertIsNone(self.windows.take_message())

    def test_window_left_hidden_by_a_crash_is_put_back(self):
        self.desktop.tuck(self.main)
        self.assertFalse(self.desktop.taskbar(self.main))
        self.assertEqual(hiding.restore_left_hidden(self.desktop), 1)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertEqual(hiding.restore_left_hidden(self.desktop), 0)

    def test_poll_errors_never_stop_the_watch(self):
        calls = []
        class Broken(FakeDesktop):
            def windows(self):
                calls.append(1)
                raise OSError('gone')
        watcher = ThunderbirdWindows(Broken()); watcher.hiding = True
        original = hiding.POLL_SECONDS
        hiding.POLL_SECONDS = 0.01
        try:
            watcher.start()
            import time
            deadline = time.monotonic() + 2
            while len(calls) < 3 and time.monotonic() < deadline: time.sleep(0.01)
        finally:
            watcher.stopped.set(); hiding.POLL_SECONDS = original
        self.assertGreaterEqual(len(calls), 3)


class AttentionWordsTests(unittest.TestCase):
    def test_messages_name_what_thunderbird_wants(self):
        said = hiding.attention_message
        self.assertEqual(said('Sign in to your account'), hiding.SIGN_IN)
        self.assertEqual(said('Yahoo'), hiding.SIGN_IN)
        self.assertEqual(said('Enter your password for you@example.com'), hiding.PASSWORD)
        self.assertEqual(said('Add Security Exception'), hiding.CERTIFICATE)
        self.assertEqual(said('Software Update'), hiding.UPDATE)
        self.assertEqual(said(''), hiding.OTHER.format(title=''))
        self.assertEqual(said('x' * 200), hiding.OTHER.format(title=''))

    def test_messages_are_plain(self):
        import documentation_quality
        for text in (hiding.SIGN_IN, hiding.SHOWN, hiding.HIDDEN_NOW, hiding.STILL_NEEDED, hiding.SIGNING_IN):
            self.assertNotIn('  ', text)
            self.assertTrue(text.endswith('.'))
        self.assertTrue(hasattr(documentation_quality, 'report'))


if __name__ == '__main__':
    unittest.main()


class CompanionWiringTests(unittest.TestCase):
    """The option in Voice Companion itself. "Minimized, as before" runs exactly the old steps."""

    def setUp(self):
        import companion, threading
        from unittest import mock
        self.companion, self.mock = companion, mock
        self.relay = mock.Mock(); self.relay.connected = threading.Event(); self.relay.connected.set()
        self.desktop = FakeDesktop(); self.main = self.desktop.open(minimized=True)
        for name, value in (('THUNDERBIRD_STARTED', False), ('THUNDERBIRD_WINDOWS', None), ('APP_WINDOW', None),
                            ('PREFERENCES', dict(companion.PREFERENCES))):
            patcher = mock.patch.object(companion, name, value); patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.object(companion, 'thunderbird_desktop', lambda: self.desktop)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch('atexit.register'); patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(self.stop_watch)

    def stop_watch(self):
        if self.companion.THUNDERBIRD_WINDOWS is not None: self.companion.THUNDERBIRD_WINDOWS.stopped.set()

    def test_default_is_minimized_as_before_and_touches_no_window(self):
        self.assertEqual(self.companion.PREFERENCES.get('thunderbird_window'), hiding.MINIMIZED)
        self.companion.quiet_thunderbird(self.relay)
        self.assertEqual(self.relay.request.call_args_list, [self.mock.call('minimize', {}, timeout=10)])
        self.assertIsNone(self.companion.THUNDERBIRD_WINDOWS)
        self.assertEqual(self.desktop.calls, [])
        self.assertTrue(self.desktop.taskbar(self.main))

    def test_hidden_option_hides_and_exit_puts_it_back(self):
        self.companion.PREFERENCES['thunderbird_window'] = hiding.HIDDEN
        self.companion.quiet_thunderbird(self.relay)
        self.assertFalse(self.desktop.taskbar(self.main))
        self.relay.request.assert_any_call('background', {'on': True}, timeout=10)
        self.relay.request.return_value = {'quit': True}
        self.assertEqual(self.companion.close_started_thunderbird(lambda: self.relay), {'quit': True})
        self.assertTrue(self.desktop.taskbar(self.main))

    def test_a_thunderbird_the_user_opened_is_left_alone(self):
        self.companion.PREFERENCES['thunderbird_window'] = hiding.HIDDEN
        self.assertFalse(self.companion.apply_thunderbird_window())
        self.assertEqual(self.desktop.calls, [])

    def test_adding_an_account_counts_as_signing_in(self):
        with self.mock.patch.object(self.companion, 'THUNDERBIRD_WATCH', {'address': 'a@b.c'}):
            self.assertTrue(self.companion.thunderbird_signing_in())
        with self.mock.patch.object(self.companion, 'ACCOUNT_ENTRY', {'provider': 'thunderbird'}):
            self.assertTrue(self.companion.thunderbird_signing_in())
        with self.mock.patch.object(self.companion, 'THUNDERBIRD_WATCH', None), \
             self.mock.patch.object(self.companion, 'THUNDERBIRD_SETUP', None), \
             self.mock.patch.object(self.companion, 'THUNDERBIRD_SIGN_IN', None), \
             self.mock.patch.object(self.companion, 'ACCOUNT_ENTRY', None):
            self.assertFalse(self.companion.thunderbird_signing_in())

    def test_changing_the_option_takes_effect_now_and_says_so(self):
        self.companion.THUNDERBIRD_STARTED = True
        self.companion.PREFERENCES['thunderbird_window'] = hiding.HIDDEN
        said = self.companion.window_options_changed({'thunderbird_window': hiding.MINIMIZED})
        self.assertEqual(said, self.companion.THUNDERBIRD_HIDDEN_OPTION)
        self.assertFalse(self.desktop.taskbar(self.main))
        self.companion.PREFERENCES['thunderbird_window'] = hiding.MINIMIZED
        said = self.companion.window_options_changed({'thunderbird_window': hiding.HIDDEN})
        self.assertEqual(said, self.companion.THUNDERBIRD_MINIMIZED_OPTION)
        self.assertTrue(self.desktop.taskbar(self.main))
        self.assertEqual(self.companion.window_options_changed({'thunderbird_window': hiding.MINIMIZED}), '')

    def test_show_and_hide_thunderbird_commands(self):
        self.companion.THUNDERBIRD_STARTED = True
        self.companion.PREFERENCES['thunderbird_window'] = hiding.HIDDEN
        self.companion.apply_thunderbird_window()
        said = []
        with self.mock.patch.object(self.companion, 'speak', said.append), \
             self.mock.patch.object(self.companion, 'THUNDERBIRD_WATCH', None), \
             self.mock.patch.object(self.companion, 'THUNDERBIRD_SETUP', None), \
             self.mock.patch.object(self.companion, 'THUNDERBIRD_SIGN_IN', None), \
             self.mock.patch.object(self.companion, 'ACCOUNT_ENTRY', None):
            self.companion.thunderbird_setup_command('show thunderbird', 'awake')
            self.assertTrue(self.desktop.taskbar(self.main))
            self.companion.thunderbird_setup_command('hide thunderbird', 'awake')
            self.assertFalse(self.desktop.taskbar(self.main))
        self.assertEqual(said, [hiding.SHOWN, hiding.HIDDEN_NOW])

    def test_hide_thunderbird_as_before_asks_thunderbird_to_minimize(self):
        said = []
        with self.mock.patch.object(self.companion, 'speak', said.append), \
             self.mock.patch('thunderbird_mailbox.relay', lambda: self.relay):
            self.companion.thunderbird_setup_command('hide thunderbird', 'awake')
        self.relay.request.assert_called_with('minimize', {}, timeout=10)
        self.assertEqual(said, [hiding.MINIMIZED_NOW])
        self.assertEqual(self.desktop.calls, [])

    def test_a_window_that_came_up_is_said_on_the_main_loop(self):
        self.companion.THUNDERBIRD_STARTED = True
        self.companion.PREFERENCES['thunderbird_window'] = hiding.HIDDEN
        self.companion.apply_thunderbird_window()
        self.companion.THUNDERBIRD_WINDOWS.messages.put(hiding.SIGN_IN)
        said = []
        with self.mock.patch.object(self.companion, 'speak_status', said.append), \
             self.mock.patch.object(self.companion, 'THUNDERBIRD_WATCH', None):
            self.companion.poll_thunderbird_watch('awake')
        self.assertEqual(said, [hiding.SIGN_IN])

    def test_browser_option_change_takes_effect_and_says_so(self):
        import web_assistant
        with self.mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', False), \
             self.mock.patch.object(self.companion, 'web_session', None):
            self.companion.PREFERENCES['browser_window'] = 'Run the browser in the background'
            said = self.companion.window_options_changed({'browser_window': 'Show the browser window, as before'})
            self.assertEqual(said, self.companion.CHROME_BACKGROUND_OPTION)
            self.assertTrue(web_assistant.CHROME_IN_BACKGROUND)
            self.companion.PREFERENCES['browser_window'] = 'Show the browser window, as before'
            said = self.companion.window_options_changed({'browser_window': 'Run the browser in the background'})
            self.assertEqual(said, self.companion.CHROME_SHOWN_OPTION)
            self.assertFalse(web_assistant.CHROME_IN_BACKGROUND)

    def test_settings_defaults_are_the_original_behaviour(self):
        from settings_model import DEFAULTS, fields
        self.assertEqual(DEFAULTS['thunderbird_window'], 'Minimized, as before')
        self.assertEqual(DEFAULTS['browser_window'], 'Show the browser window, as before')
        email = {f.key: f for f in fields('Email options', {})}
        web = {f.key: f for f in fields('Web browser options', {})}
        self.assertEqual(email['thunderbird_window'].choices, ('Minimized, as before', 'Hidden from the taskbar'))
        self.assertEqual(web['browser_window'].choices, ('Show the browser window, as before', 'Run the browser in the background'))

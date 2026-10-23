"""Alt keys reach Companion, not the Windows system menu (Will, October 2026).

On Windows, Tk binds Alt on the "all" tag and hands it to Windows, which opens
the system menu. Those bindings outrank Companion's own, so a lone Alt never
opened the Email menu. These tests start the real window with the same
bindings Tk makes on Windows (win/tkWinMenu.c, TkpInitializeMenuBindings), so
they show the Windows behaviour on any computer with a display. On Windows
the real bindings are already there.
"""
import os
import sys
import threading
import time
import unittest

WINDOWS_TK_ALT_BINDINGS = """
if {[info commands ::tk::WinMenuKey] eq ""} {
    proc ::tk::WinMenuKey {w k} {lappend ::given_to_windows $k}
}
foreach sequence {<KeyRelease-Alt_L> <KeyRelease-Alt_R> <Alt-KeyPress> <Alt-KeyRelease> <KeyPress-F10> <KeyRelease-F10>} {
    bind all $sequence {tk::WinMenuKey %W %N}
}
set ::given_to_windows {}
"""


def _display():
    return sys.platform == 'win32' or bool(os.environ.get('DISPLAY'))


def _window_tests(cls):
    # Windows: in its own process after the release tests (real_window_tests.py).
    # Elsewhere: only with a display, where it adds Tk's Windows Alt bindings itself.
    if sys.platform == 'win32':
        from real_window_tests import real_window
        return real_window(cls)
    return unittest.skipUnless(_display(), 'needs a display for a real Tk window')(cls)


@_window_tests
class AltKeyWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter
        except ImportError:
            self.skipTest('Tk is not installed')
        original = tkinter.Tk.__init__
        def with_windows_bindings(tk_self, *args, **kwargs):
            original(tk_self, *args, **kwargs)
            if sys.platform != 'win32':
                tk_self.tk.eval(WINDOWS_TK_ALT_BINDINGS)
        tkinter.Tk.__init__ = with_windows_bindings
        self.addCleanup(setattr, tkinter.Tk, '__init__', original)
        from app_window import CompanionWindow
        self.window = CompanionWindow('alt key test'); self.window.start()

    def tearDown(self):
        if self.window.thread.is_alive():
            self.call(lambda: self.window.root.destroy(), check=False)
        self.window.thread.join(5)

    def call(self, function, check=True):
        done = threading.Event(); result = []; errors = []
        def run():
            try: result.append(function())
            except Exception as exc: errors.append(exc)
            finally: done.set()
        self.window.ui_actions.put(('ui_call', run))
        ok = done.wait(5)
        if check and not ok: self.fail('The window did not run the request. ' + repr(self.window.debug_state()))
        if errors: raise errors[0]
        return result[0] if result else None

    def screen(self, **context):
        context = dict(dict(source=None, text='', caret=0, ack=0, focus=True), **context)
        self.window.set_context(context)
        end = time.monotonic() + 5
        while self.window.context.get('mode') != context['mode'] and time.monotonic() < end: time.sleep(.02)
        if self.window.context.get('mode') != context['mode']: self.fail(repr(self.window.debug_state()))
        self.call(lambda: self.window.typed.focus_force())
        self.window.commands.queue.clear()

    def keys(self, *events):
        def send():
            for kind, keysym, state in events:
                self.window.typed.event_generate('<' + kind + '>', keysym=keysym, state=state)
        self.call(send); time.sleep(.2)

    def given_to_windows(self):
        if sys.platform == 'win32': return []
        return self.call(lambda: self.window.root.tk.eval('set ::given_to_windows')).split()

    def test_alt_alone_opens_the_email_menu_and_never_the_system_menu(self):
        self.screen(mode='mailbox')
        self.keys(('KeyPress', 'Alt_L', 0), ('KeyRelease', 'Alt_L', 8))
        self.assertIn(('keyboard', 'Alt'), list(self.window.commands.queue))
        self.assertEqual(self.given_to_windows(), [])

    def test_right_alt_is_routed_to_companion_too(self):
        # Tk on Windows cannot make a synthetic Right Alt key ("no keycode for
        # keysym Alt_R"), so check that Right Alt runs the same handlers as Left Alt.
        self.screen(mode='mailbox')
        bindings = self.call(lambda: [self.window.root.tk.call('bind', 'all', sequence)
                                      for sequence in ('<KeyRelease>', '<KeyRelease-Alt_L>', '<KeyRelease-Alt_R>')])
        self.assertEqual(bindings[1], bindings[0]); self.assertEqual(bindings[2], bindings[0])
        self.assertNotIn('WinMenuKey', bindings[2])

    def test_alt_with_a_key_does_not_open_the_menu(self):
        self.screen(mode='mailbox')
        self.keys(('KeyPress', 'Alt_L', 0), ('KeyPress', 'Down', 8), ('KeyRelease', 'Down', 8), ('KeyRelease', 'Alt_L', 8))
        self.assertNotIn(('keyboard', 'Alt'), list(self.window.commands.queue))

    def test_alt_where_there_is_no_menu_says_why_and_stays_in_companion(self):
        self.screen(mode='mailbox', confirmation='no')
        self.keys(('KeyPress', 'Alt_L', 0), ('KeyRelease', 'Alt_L', 8))
        self.assertNotIn(('keyboard', 'Alt'), list(self.window.commands.queue))
        self.assertEqual(self.given_to_windows(), [])

    def test_alt_letter_shortcuts_reach_companion(self):
        # Alt+C in the file manager is handled by Companion's own key handler.
        self.screen(mode='files')
        self.keys(('KeyPress', 'Alt_L', 0), ('KeyPress', 'c', 8), ('KeyRelease', 'c', 8), ('KeyRelease', 'Alt_L', 8))
        self.assertEqual(self.given_to_windows(), [])


if __name__ == '__main__':
    unittest.main()

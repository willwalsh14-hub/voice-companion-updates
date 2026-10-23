"""Num Lock on must not turn plain keys into Alt keys in the real Windows window.

Windows Tk sets event.state bit 0x8 while Num Lock is on, and 0x20000 for Alt.
"""
import sys
import threading
import time
import unittest

from app_window import CompanionWindow
from key_state import ALT_STATE

NUM_LOCK = 0x8


@unittest.skipUnless(sys.platform == 'win32', 'Windows Tk keyboard integration')
class TkWindowsStateBitsTests(unittest.TestCase):
    def test_tk_names_alt_and_mod1_with_these_bits(self):
        # Tk itself says which bit is Alt: <Alt-...> and <Mod1-...> events.
        import tkinter as tk
        seen = []
        root = tk.Tk()
        try:
            entry = tk.Entry(root)
            entry.pack()
            entry.bind('<KeyPress>', lambda event: seen.append(('key', event.state & 0x2000F)))
            entry.bind('<Alt-KeyPress>', lambda event: seen.append(('alt binding', event.state & 0x2000F)))
            root.update()
            entry.focus_force()
            root.update()
            entry.event_generate('<Alt-KeyPress>', keysym='a')
            entry.event_generate('<Mod1-KeyPress>', keysym='a')
            root.update()
            entry = None
        finally:
            root.destroy()
            root = None
        self.assertEqual(seen, [('alt binding', 0x20000), ('key', NUM_LOCK)])


@unittest.skipUnless(sys.platform == 'win32', 'Windows Tk keyboard integration')
class NumLockWindowTests(unittest.TestCase):
    def setUp(self):
        self.window = CompanionWindow('num lock test')
        self.window.start()

    def tearDown(self):
        if self.window.thread.is_alive():
            self.call(lambda: self.window.root.destroy())
        self.window.thread.join(5)
        self.assertFalse(self.window.thread.is_alive())

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
        if not done.wait(5):
            self.fail(self.window.debug_state())
        if errors:
            raise errors[0]

    def wait(self, condition):
        end = time.monotonic() + 5
        while not condition() and time.monotonic() < end:
            time.sleep(.015)
        if not condition():
            self.fail(self.window.debug_state())

    def press(self, widget_name, key, state):
        def run():
            widget = getattr(self.window, widget_name)
            widget.focus_force()
            widget.event_generate('<KeyPress>', keysym=key, state=state)
        self.call(run)

    def commands(self):
        return list(self.window.commands.queue)

    def test_arrows_and_quick_keys_on_a_web_page(self):
        self.window.set_context(dict(mode='web', source='page', text='Heading\nBody\nLink', caret=0, ack=0,
                                     readonly=True, web_browse=True, web_revision=1))
        self.wait(lambda: self.window.context.get('web_browse'))
        for key, expected in (('k', 'Web:Quick:k'), ('Left', 'Web:Read:Left'), ('Right', 'Web:Read:Right')):
            with self.subTest(key=key):
                self.press('editor', key, NUM_LOCK)
                self.wait(lambda e=expected: ('keyboard', e) in self.window.commands.queue)
        self.assertNotIn(('keyboard', 'Web:Back'), self.commands())
        self.assertNotIn(('keyboard', 'Web:Forward'), self.commands())
        self.press('editor', 'Left', ALT_STATE | NUM_LOCK)
        self.wait(lambda: ('keyboard', 'Web:Back') in self.window.commands.queue)

    def test_o_and_t_do_not_open_options_or_the_command_box(self):
        self.window.set_context(dict(mode='mailbox', source=None, text='', caret=0, ack=0, focus=True))
        self.wait(lambda: self.window.context.get('mode') == 'mailbox')
        for key in ('o', 't', 'F4'):
            self.press('typed', key, NUM_LOCK)
        time.sleep(.2)
        self.assertNotIn(('keyboard', 'settings'), self.commands())
        self.assertNotIn(('keyboard', 'Alt+F4'), self.commands())
        self.assertFalse(self.window.command_entry_active)
        self.press('typed', 'o', ALT_STATE | NUM_LOCK)
        self.wait(lambda: ('keyboard', 'settings') in self.window.commands.queue)

    def test_y_and_n_answer_with_num_lock_but_are_not_alt(self):
        self.window.set_context(dict(mode='exit_confirm', source=None, text='', confirmation='no', caret=0, ack=0, focus=True))
        self.wait(lambda: self.window.context.get('mode') == 'exit_confirm')
        self.press('typed', 'y', NUM_LOCK)
        time.sleep(.2)
        self.assertNotIn(('keyboard', 'Alt+Y'), self.commands())
        self.press('typed', 'y', ALT_STATE | NUM_LOCK)
        self.wait(lambda: ('keyboard', 'Alt+Y') in self.window.commands.queue)

    def test_menu_letters_work_with_num_lock(self):
        self.window.set_context(dict(mode='awake', source=None, text='', caret=0, ack=0, focus=True, menu_letters=True))
        self.wait(lambda: self.window.context.get('menu_letters'))
        self.press('typed', 'e', NUM_LOCK)
        self.wait(lambda: ('keyboard', 'Letter:e') in self.window.commands.queue)


if __name__ == '__main__':
    unittest.main()

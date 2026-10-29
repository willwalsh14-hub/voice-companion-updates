"""YouTube keys in the real window: each key reaches the YouTube screen once.

Letters, digits, Space, Comma and Period are actions on the YouTube screen, as
in Email. They are never typed into the command box and never search the list
by first letter. A command typed after Alt+T keeps its letters. A Yes or No
question gets Y, N and the arrows. Alt+O and F-keys keep their own jobs.
"""
from real_window_tests import real_window
import threading
import time
import unittest


@real_window
class YouTubeWindowKeyTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window = CompanionWindow('youtube key test')
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
        return condition()

    def screen(self, **context):
        while not self.window.commands.empty():
            self.window.commands.get_nowait()
        self.window.set_context(dict(dict(mode='youtube', source=None, text='', caret=0, ack=0, focus=True,
                                          menu_letters=False), **context))
        if not self.wait(lambda: self.window.context == dict(self.window.context, **context)):
            self.fail('The window did not show the YouTube screen.')

    def press(self, keysym, state=0):
        def send():
            self.window.typed.focus_force()  # Synthetic keys go to the focused widget.
            self.window.typed.event_generate('<KeyPress>', keysym=keysym, state=state)
            self.window.typed.event_generate('<KeyRelease>', keysym=keysym, state=state)
        self.call(send)

    def requests(self):
        return [item[1] for item in list(self.window.commands.queue) if isinstance(item, tuple) and item[0] == 'keyboard']

    def typed(self):
        result = []
        self.call(lambda: result.append(self.window.typed.get()))
        return result[0]

    def test_action_keys_reach_the_screen_and_are_never_typed(self):
        self.screen()
        keys = (('k', 0, 'K'), ('space', 0, 'Space'), ('j', 0, 'J'), ('l', 0, 'L'), ('5', 0, '5'), ('0', 0, '0'),
                ('comma', 0, 'comma'), ('period', 0, 'period'), ('Up', 4, 'Ctrl+Up'), ('Down', 4, 'Ctrl+Down'),
                ('Right', 4, 'Ctrl+Right'), ('c', 0, 'C'), ('s', 0, 'S'), ('t', 0, 'T'), ('f', 4, 'Ctrl+F'),
                ('F5', 0, 'F5'), ('F1', 0, 'F1'), ('Left', 0, 'Left'), ('Right', 0, 'Right'), ('Down', 0, 'Down'),
                ('Up', 0, 'Up'), ('Home', 0, 'Home'), ('End', 0, 'End'), ('Return', 0, 'Enter'), ('Escape', 0, 'Escape'))
        for keysym, state, expected in keys:
            with self.subTest(key=expected):
                before = len(self.requests())
                self.press(keysym, state)
                ok = self.wait(lambda: len(self.requests()) > before)
                if not ok:
                    self.fail(expected + ' did not arrive. ' + str(self.window.debug_state()))
                self.assertEqual(self.requests()[before:], [expected])
        self.assertEqual(self.typed(), '')

    def test_a_question_takes_y_n_and_arrows(self):
        self.screen(confirmation='no')
        for keysym, expected in (('Up', 'Up'), ('y', 'y'), ('n', 'n'), ('Return', 'Enter')):
            before = len(self.requests())
            self.press(keysym)
            if not self.wait(lambda: len(self.requests()) > before):
                self.fail(keysym + ' did not arrive. ' + str(self.window.debug_state()))
            self.assertEqual(self.requests()[before:], [expected])


if __name__ == '__main__':
    unittest.main()

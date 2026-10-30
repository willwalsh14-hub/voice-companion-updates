"""Radio, Podcasts and the Media player in the real window, at the largest text on a small screen.

Every key these screens name must reach them once, with the list on the screen at
400 percent text. Windows drops keys sent to a widget that got no room, so the command
box must keep its room too (nightly sweep, October 30, 2026).
"""
from real_window_tests import real_window
import threading
import time
import unittest

CONTROL, SHIFT = 4, 1


@real_window
class MediaWindowKeyTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window = CompanionWindow('media key test')
        self.window.start()
        self.assertTrue(self.window.ready.wait(10))
        self.call(lambda: self.window.root.geometry('800x560'))

    def tearDown(self):
        if self.window.thread.is_alive():
            self.call(lambda: self.window.root.destroy())
        self.window.thread.join(5)

    def call(self, function):
        done = threading.Event()
        result = []

        def run():
            try:
                self.window.root.update_idletasks()
                result.append(function())
            except Exception as exc:
                result.append(exc)
            finally:
                done.set()
        self.window.ui_actions.put(('ui_call', run))
        if not done.wait(5):
            self.fail('The window did not answer.')
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]

    def wait(self, condition):
        end = time.monotonic() + 5
        while not condition() and time.monotonic() < end:
            time.sleep(.015)
        return condition()

    def screen(self, title, items, **context):
        from display_options import settings
        while not self.window.commands.empty():
            self.window.commands.get_nowait()
        context = dict(dict(source=None, text='', caret=0, ack=0, focus=True, menu_letters=True,
                            display=settings({'display_text_size': '400 percent'}), display_title=title,
                            display_items=items, display_index=0), **context)
        self.window.set_context(context)
        if not self.wait(lambda: self.window.context.get('display_title') == title):
            self.fail('The window did not show ' + title + '.')
        time.sleep(.3)   # the layout settles after idle

    def command_box(self):
        def facts():
            root, typed = self.window.root, self.window.typed
            root.update()
            return dict(mapped=bool(typed.winfo_ismapped()), bottom=typed.winfo_y() + typed.winfo_height(),
                        height=root.winfo_height())
        return self.call(facts)

    def press(self, keysym, state=0):
        def send():
            self.window.typed.focus_force()  # Synthetic keys go to the focused widget.
            self.window.typed.event_generate('<KeyPress>', keysym=keysym, state=state)
            self.window.typed.event_generate('<KeyRelease>', keysym=keysym, state=state)
        self.call(send)

    def requests(self):
        return [item[1] for item in list(self.window.commands.queue) if isinstance(item, tuple) and item[0] == 'keyboard']

    def typed(self):
        return self.call(lambda: self.window.typed.get())

    def check_keys(self, keys):
        for keysym, state, expected in keys:
            with self.subTest(key=expected):
                before = len(self.requests())
                self.press(keysym, state)
                if not self.wait(lambda: len(self.requests()) > before):
                    self.fail(expected + ' did not arrive. ' + str(self.window.debug_state()))
                self.assertEqual(self.requests()[before:], [expected])
        self.assertEqual(self.typed(), '')

    def test_radio_and_podcast_keys_reach_the_screen(self):
        self.screen('Radio', ['WGN Radio 720', 'WBEZ Chicago', 'WXRT'], mode='media', media_navigation=True)
        box = self.command_box()
        if not box['mapped'] or box['bottom'] > box['height']:
            self.fail('The command box has no room: ' + repr(box))
        self.check_keys((('Down', 0, 'Down'), ('Up', 0, 'Up'), ('Left', 0, 'Left'), ('Right', 0, 'Right'),
                         ('Home', 0, 'Home'), ('End', 0, 'End'), ('Return', 0, 'Enter'), ('space', 0, 'Space'),
                         ('Tab', 0, 'Tab'), ('Tab', SHIFT, 'ShiftTab'), ('Delete', 0, 'Delete'), ('F5', 0, 'F5'),
                         ('F1', 0, 'F1'), ('f', CONTROL, 'Ctrl+F'), ('p', CONTROL | SHIFT, 'Ctrl+Shift+P'),
                         ('r', CONTROL, 'Ctrl+R'), ('s', CONTROL, 'Ctrl+S'), ('d', CONTROL, 'Ctrl+D'),
                         ('Left', CONTROL, 'Ctrl+Left'), ('Right', CONTROL, 'Ctrl+Right'), ('w', 0, 'Letter:w'),
                         ('Escape', 0, 'Escape')))

    def test_media_player_keys_reach_the_player(self):
        self.screen('Media player', ['Open a media file', 'Play or pause', 'Stop'], mode='local_media', menu_letters=False)
        box = self.command_box()
        if not box['mapped'] or box['bottom'] > box['height']:
            self.fail('The command box has no room: ' + repr(box))
        self.check_keys((('space', 0, 'Space'), ('Left', 0, 'Left'), ('Right', 0, 'Right'), ('Up', 0, 'Up'),
                         ('Down', 0, 'Down'), ('Home', 0, 'Home'), ('End', 0, 'End'), ('Return', 0, 'Enter'),
                         ('Tab', 0, 'Tab'), ('i', CONTROL, 'Ctrl+I'), ('o', CONTROL, 'Ctrl+O'), ('s', CONTROL, 'Ctrl+S'),
                         ('F1', 0, 'F1'), ('Escape', 0, 'Escape')))


if __name__ == '__main__':
    unittest.main()

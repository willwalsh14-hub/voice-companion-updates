"""The largest text on a small screen: a long screen name never pushes the command box away.

At 400 percent on an 800 by 600 screen, "Help: Listen To Radio   1 of 6" took more
height than the window had. The command box, which has the keyboard, got no room,
and Windows drops key presses sent to a widget with no room. The layout was also
fitted only when the window or the last message changed, not when the screen name did.
"""
from real_window_tests import real_window
import threading
import time
import unittest


@real_window
class LargeHeadingWindowTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window = CompanionWindow('large heading test')
        self.window.start()
        self.assertTrue(self.window.ready.wait(10))

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

    def show(self, title, size='400 percent'):
        from display_options import settings
        display = settings({'display_text_size': size})
        self.window.set_context(dict(mode='help', source=None, text='', caret=0, ack=0, focus=True, display=display,
                                     display_title=title, display_items=['Find and play a station', 'Play a station from any screen'],
                                     display_index=0))
        end = time.monotonic() + 5
        while self.window.context.get('display_title') != title and time.monotonic() < end:
            time.sleep(.02)
        time.sleep(.3)   # the layout settles after idle

    def command_box(self):
        def facts():
            root, typed = self.window.root, self.window.typed
            root.update()
            return dict(mapped=bool(typed.winfo_ismapped()), bottom=typed.winfo_y() + typed.winfo_height(),
                        height=root.winfo_height())
        return self.call(facts)

    def test_the_command_box_keeps_its_room_when_the_screen_name_gets_long(self):
        self.call(lambda: self.window.root.geometry('800x480'))
        self.show('Radio')
        self.assertTrue(self.command_box()['mapped'])
        for title in ('Help: Listen To Radio', 'Help: Saved Podcasts And Downloads, Download and play an episode'):
            with self.subTest(title=title):
                self.show(title)
                box = self.command_box()
                if not box['mapped'] or box['bottom'] > box['height']:
                    self.fail('The command box has no room: ' + repr(box))

    def heading(self):
        """The screen name's font and wrap length, as text: no widget is kept here."""
        def facts():
            label = next(w for w in self.window.root.winfo_children() if w.winfo_class() == 'Label' and w.winfo_y() < 40)
            return str(label.cget('font')), int(label.cget('wraplength'))
        return self.call(facts)

    def test_the_screen_name_is_large_and_wraps_again_when_there_is_room(self):
        self.call(lambda: self.window.root.geometry('800x480'))
        self.show('Radio', size='150 percent')
        title_font, wrap = self.heading()
        self.assertGreater(wrap, 0)
        self.show('Help: Saved Podcasts And Downloads, Download and play an episode')
        self.show('Help: Saved Podcasts And Downloads, Download and play an episode', size='150 percent')
        self.assertEqual(self.heading()[0], title_font)
        self.assertGreater(self.heading()[1], 0)


    def tab_from_the_command_box(self):
        """Press Tab in the command box; return the focused widget's class and what was said."""
        import queue
        while True:
            try: self.window.key_feedback.get_nowait()
            except queue.Empty: break
        def tab():
            self.window.typed.focus_force()
            self.window.root.update()
            self.window.typed.event_generate('<KeyPress>', keysym='Tab')
            self.window.root.update()
            return self.window.root.focus_get().winfo_class()
        focused = self.call(tab)
        said = []
        while True:
            try: said.append(self.window.key_feedback.get_nowait())
            except queue.Empty: break
        return focused, said

    def test_tab_never_moves_the_keyboard_silently(self):
        # On the YouTube screen with no list yet, Tab went silently to a scroll bar at large text,
        # and to the Options button at normal text, where Enter then opened Options.
        self.call(lambda: self.window.root.geometry('800x480'))
        self.window.set_context(dict(mode='youtube', source=None, text='', caret=0, ack=0, focus=True, menu_letters=False,
                                     display=__import__('display_options').settings({'display_text_size': '200 percent'}),
                                     display_title='YouTube'))
        time.sleep(.4)
        focused, said = self.tab_from_the_command_box()
        self.assertNotEqual(focused, 'Scrollbar')
        if focused == 'Button':
            self.assertTrue(said and said[-1].endswith(' button. Enter activates. Shift+Tab goes back.'), said)
        self.call(lambda: self.window.root.geometry('1000x800'))
        self.window.set_context(dict(mode='youtube', source=None, text='', caret=0, ack=0, focus=True, menu_letters=False,
                                     display=__import__('display_options').settings({'display_text_size': '100 percent'}),
                                     display_title='YouTube.'))
        time.sleep(.4)
        focused, said = self.tab_from_the_command_box()
        self.assertEqual(focused, 'Button')
        self.assertEqual(said[-1:], ['Options (Alt+O) button. Enter activates. Shift+Tab goes back.'])


if __name__ == '__main__':
    unittest.main()

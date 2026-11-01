"""The keys of the window's text box on every text screen (Will, October 31, 2026). Windows Tk only:
these press real keys in the real window."""
import threading
import time
import unittest

from app_window import CompanionWindow
from real_window_tests import real_window


@real_window
class TextBoxKeyTests(unittest.TestCase):
    def setUp(self):
        self.window = CompanionWindow('text keys test'); self.window.start()

    def tearDown(self):
        if self.window.thread.is_alive(): self.call(lambda: self.window.root.destroy())
        self.window.thread.join(5); self.assertFalse(self.window.thread.is_alive())

    def call(self, function):
        done = threading.Event(); errors = []
        def run():
            try:
                self.window.root.update_idletasks(); function()
            except Exception as exc: errors.append(exc)
            finally: done.set()
        self.window.ui_actions.put(('ui_call', run)); (done.wait(5) or self.fail(self.window.debug_state()))
        if errors: raise errors[0]

    def wait(self, condition):
        end = time.monotonic() + 5
        while not condition() and time.monotonic() < end: time.sleep(.015)
        if not condition(): self.fail(self.window.debug_state())

    def show(self, mode, text, caret=0, **extra):
        self.window.set_context(dict(mode=mode, source='t-' + mode, text=text, caret=caret, selection=None, echo='characters',
                                     phonetic=False, ack=0, focus=True, **extra))
        self.wait(lambda: self.window.context.get('source') == 't-' + mode)
        self.call(lambda: (self.window.editor.focus_force(), self.window.editor.mark_set('insert', '1.0 + %d chars' % caret)))

    def press(self, key, state=0):
        self.call(lambda: self.window.editor.event_generate('<KeyPress>', keysym=key, state=state))

    def said(self, words):
        self.wait(lambda: words in [str(x) for x in self.window.key_feedback.queue])

    def test_control_right_moves_by_word_and_says_blank_at_line_ends(self):
        self.show('document', 'Dear Will,\n\nThe plan.')
        for words in ('Will,', 'Blank.', 'Blank.', 'The'):
            self.press('Right', 4); self.said(words)
        self.call(lambda: self.assertEqual(len(self.window.editor.get('1.0', 'insert')), 12))

    def test_blank_lines_in_an_email_say_blank(self):
        self.show('mailbox', 'Hi Will,\n ‌\nSee you.', readonly=True, rich_spans=[(0, 8, 'bold', '')], email_html=True)
        self.press('Down'); self.said('Blank.')
        self.press('Down'); self.said('See you.')

    def test_shift_arrows_say_what_changed_and_control_a_selects_all(self):
        self.show('file_reader', 'abc def', readonly=True)
        self.press('Right', 1); self.said('Selected a')
        self.press('Right', 1); self.said('Selected b')
        self.press('Left', 1); self.said('Unselected b')
        self.press('a', 4); self.said('All text selected.')

    def test_insert_down_says_all_on_every_text_screen(self):
        for mode in ('mailbox', 'help', 'file_reader', 'document'):
            self.show(mode, 'One.\nTwo.', readonly=mode != 'document', help_reader=mode == 'help')
            self.call(lambda: (self.window.editor.event_generate('<KeyPress>', keysym='Insert'),
                               self.window.editor.event_generate('<KeyPress>', keysym='Down'),
                               self.window.editor.event_generate('<KeyRelease>', keysym='Insert')))
            self.wait(lambda: ('keyboard', 'Insert+Down') in self.window.commands.queue)
            with self.window.commands.mutex: self.window.commands.queue.clear()

    def test_a_help_topic_uses_the_arrows_like_any_text(self):
        self.show('help', 'First line.\nSecond line.', readonly=True, help_reader=True)
        self.press('Down'); self.said('Second line.')
        self.assertFalse(any(str(c[1]).startswith('Help:') for c in list(self.window.commands.queue) if c[0] == 'keyboard'))


if __name__ == '__main__':
    unittest.main()

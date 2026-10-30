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

    def type_text(self, text):
        for char in text:
            self.press('space' if char == ' ' else char)

    def test_the_search_box_takes_typed_letters_and_enter_searches(self):
        # Will could not type a search: the letters played, paused and skipped the video.
        self.screen(task_input=True, naming_label='YouTube search; type, then press Enter. Escape cancels:',
                    entry_request=(1, ''))
        self.type_text('kjl fm cats')
        if not self.wait(lambda: self.typed() == 'kjl fm cats'):
            self.fail('Typed: ' + repr(self.typed()) + ' ' + str(self.window.debug_state()))
        self.assertEqual(self.requests(), [])   # no letter reached the screen as a video key
        self.press('Return')
        if not self.wait(lambda: 'kjl fm cats' in list(self.window.commands.queue)):
            self.fail('Enter did not send the search. ' + str(self.window.debug_state()))
        self.assertEqual(self.typed(), '')

    def test_spoken_search_words_wait_in_the_box_and_escape_cancels(self):
        self.screen(task_input=True, entry_request=(2, ''))
        self.screen(task_input=True, entry_request=(3, 'Jazz piano'))
        if not self.wait(lambda: self.typed() == 'Jazz piano'):
            self.fail('The said words are not in the box: ' + repr(self.typed()))
        self.press('Escape')
        if not self.wait(lambda: 'Escape' in self.requests()):
            self.fail('Escape did not arrive. ' + str(self.window.debug_state()))
        # The box empties when the screen says so, and the video keys work again.
        self.screen(entry_request=(4, ''))
        if not self.wait(lambda: self.typed() == ''):
            self.fail('The box kept: ' + repr(self.typed()))
        self.press('k')
        if not self.wait(lambda: 'K' in self.requests()):
            self.fail('K did not arrive. ' + str(self.window.debug_state()))

    def test_a_letter_after_typed_letters_is_typed_not_a_video_key(self):
        # B is no video key, so it starts a typed command; then F must be typed, not full screen.
        self.screen()
        self.type_text('bf')
        if not self.wait(lambda: self.typed() == 'bf'):
            self.fail('Typed: ' + repr(self.typed()) + ' ' + str(self.window.debug_state()))
        self.assertEqual(self.requests(), [])

    def test_letters_move_by_first_letter_in_the_youtube_menu(self):
        self.screen(menu_letters=True)
        self.press('h')
        if not self.wait(lambda: self.requests()):
            self.fail('H did not arrive. ' + str(self.window.debug_state()))
        self.assertEqual(self.requests(), ['Letter:h'])
        self.press('f', 4)
        if not self.wait(lambda: 'Ctrl+F' in self.requests()):
            self.fail('Control+F did not arrive. ' + str(self.window.debug_state()))
        self.assertEqual(self.typed(), '')

    def test_a_question_takes_y_n_and_arrows(self):
        self.screen(confirmation='no')
        for keysym, expected in (('Up', 'Up'), ('y', 'y'), ('n', 'n'), ('Return', 'Enter')):
            before = len(self.requests())
            self.press(keysym)
            if not self.wait(lambda: len(self.requests()) > before):
                self.fail(keysym + ' did not arrive. ' + str(self.window.debug_state()))
            self.assertEqual(self.requests()[before:], [expected])


@real_window
class YouTubeSearchCustomerTests(unittest.TestCase):
    """As a customer: real key presses in the real window, sent through Companion's own key loop
    (process_keyboard_batch) and back to the window. Only the browser is left out: searches and
    lists are recorded instead of loaded."""
    def setUp(self):
        import io
        from contextlib import ExitStack, redirect_stdout
        from unittest.mock import patch
        import companion as app
        import youtube_screen as ys
        from app_window import CompanionWindow
        self.app, self.ys = app, ys
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.window = CompanionWindow('youtube search test')
        self.window.start()
        self.addCleanup(self.close_window)
        defaults = {'APP_WINDOW': self.window, 'TEXT_MODE': True, 'INPUT_MODE': 'mixed', 'document': None, 'email_draft': None,
                    'mail_session': None, 'web_session': None, 'pending_website': None, 'pending_send': None, 'NAMED_LIST': None,
                    'account_setup': False, 'MAIN_MENU_INDEX': None, 'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None,
                    'help_session': None, 'tutorial_session': None, 'MEDIA_HUB': None, 'PREFERENCES': dict(app.PREFERENCES),
                    'SLEEP_RETURN_MODE': 'awake', 'FILE_READER': None, 'FILE_MANAGER': None, 'TASK_NAV': None, 'SCREEN_MENU': None,
                    'ACCOUNT_PICKER': None, 'ACCOUNT_ENTRY': None, 'THUNDERBIRD_SETUP': None, 'UI_CONTEXT_CACHE': None,
                    'KEYBOARD_SPEECH': None}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.said = []
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            self.stack.enter_context(patch.object(app, name, side_effect=lambda text, *a, **k: self.said.append(str(text))))
        self.stack.enter_context(patch.object(app, 'interrupt_speech'))
        self.stack.enter_context(patch.object(ys, 'SCREEN', None))
        self.searched = []
        self.stack.enter_context(patch.object(ys.YouTubeScreen, 'search', lambda screen, query=None: self.searched.append(query) or 'Results.'))
        self.stack.enter_context(patch.object(ys.YouTubeScreen, 'load', lambda screen, name, lead='': name + ' list.'))
        self.mode = 'awake'

    def close_window(self):
        if self.window.thread.is_alive():
            done = threading.Event()
            self.window.ui_actions.put(('ui_call', lambda: (self.window.root.destroy(), done.set())))
            done.wait(5)
        self.window.thread.join(5)

    def call(self, function):
        done, result = threading.Event(), []
        def run():
            try:
                result.append(function())
            finally:
                done.set()
        self.window.ui_actions.put(('ui_call', run))
        self.assertTrue(done.wait(5))
        return result[0] if result else None

    def show(self):
        """As handle() does after each step: send the screen to the window (with the keyboard only
        when the screen changes), and wait for it."""
        self.app.sync_app_context(self.mode)
        wanted = self.app.app_context(self.mode)
        end = time.monotonic() + 5
        while time.monotonic() < end and any(self.window.context.get(k) != v for k, v in wanted.items()):
            time.sleep(.02)

    def say(self, words):
        self.mode = self.app.handle(words, self.mode)
        self.show()

    def keys(self, *presses):
        """Real key presses on the focused widget; then Companion's key loop runs, as in the app."""
        def send():
            widget = self.window.root.focus_get() or self.window.typed
            for keysym, state in presses:
                widget = self.window.root.focus_get() or widget
                widget.event_generate('<KeyPress>', keysym=keysym, state=state)
                widget.event_generate('<KeyRelease>', keysym=keysym, state=state)
        self.call(lambda: self.window.typed.focus_force())
        self.call(send)
        time.sleep(.2)
        self.mode = self.app.process_keyboard_batch(self.mode)
        self.show()

    def type(self, text):
        self.keys(*[('space' if c == ' ' else c, 0) for c in text])

    def box(self):
        return self.call(lambda: self.window.typed.get())

    def test_type_a_search_and_press_enter(self):
        self.say('YouTube')
        self.assertEqual(self.said[-1], 'YouTube menu. Search (S), 1 of 9. Control+F.')
        self.keys(('h', 0))
        self.assertEqual(self.said[-1], 'Home (H), 2 of 9.')
        self.keys(('f', 4))
        self.assertEqual(self.said[-1], self.ys.SEARCH_PROMPT)
        self.type('kjl fm cats')
        if self.box() != 'kjl fm cats':
            self.fail('The box holds ' + repr(self.box()) + '. ' + str(self.window.debug_state()))
        self.assertEqual(self.searched, [])
        self.keys(('Return', 0))
        self.assertEqual(self.searched, ['kjl fm cats'])
        self.assertEqual(self.box(), '')

    def test_say_a_search_then_confirm_or_press_enter(self):
        self.say('YouTube')
        self.keys(('Return', 0))   # Search, first in the menu
        self.assertEqual(self.said[-1], self.ys.SEARCH_PROMPT)
        self.say('Jazz piano.')
        self.assertEqual(self.said[-1], 'Jazz piano. Press Enter or say confirm to search.')
        if self.box() != 'Jazz piano':
            self.fail('The box holds ' + repr(self.box()) + '. ' + str(self.window.debug_state()))
        self.keys(('Return', 0))
        self.assertEqual(self.searched, ['Jazz piano'])
        self.keys(('f', 4))
        self.say('Blues guitar.')
        self.say('Confirm.')
        self.assertEqual(self.searched, ['Jazz piano', 'Blues guitar'])
        self.assertEqual(self.box(), '')
        # Escape closes the box; the menu letters work again.
        self.keys(('f', 4))
        self.type('ca')
        self.keys(('Escape', 0))
        self.assertTrue(self.said[-1].startswith('Search canceled. YouTube menu.'), self.said[-1])
        self.assertEqual(self.box(), '')
        self.keys(('h', 0))
        self.assertEqual(self.said[-1], 'Home (H), 2 of 9.')


if __name__ == '__main__':
    unittest.main()

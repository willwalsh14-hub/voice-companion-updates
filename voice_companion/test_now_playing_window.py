"""F3 in the real window: "What's playing?" from every screen (Will, October 2026).

F3 sends one request on every screen: in menus and lists, in a document, an email
draft, a note, a web page in browse or app mode, a web text box and a password box,
with the Alt menu open, and while a Yes or No question waits. It is never typed
into the text. Shift+F3 and Control+F3 are not F3.
"""
from real_window_tests import real_window
import threading
import time
import unittest


@real_window
class WindowNowPlayingKeyTests(unittest.TestCase):
    def setUp(self):
        from app_window import CompanionWindow
        self.window = CompanionWindow('now playing key test')
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
        self.window.set_context(dict(dict(source=None, text='', caret=0, ack=0, focus=True), **context))
        if not self.wait(lambda: self.window.context.get('mode') == context['mode']):
            self.fail('The window did not show ' + context['mode'] + '.')

    def press(self, keysym, state=0, editor=False):
        def send():
            target = self.window.editor if editor else self.window.typed
            target.focus_force()  # Synthetic keys go to the focused widget.
            target.event_generate('<KeyPress>', keysym=keysym, state=state)
            target.event_generate('<KeyRelease>', keysym=keysym, state=state)
        self.call(send)

    def requests(self):
        return [item for item in list(self.window.commands.queue) if isinstance(item, tuple) and item[0] == 'keyboard']

    def text(self, editor=False):
        result = []
        self.call(lambda: result.append(self.window.editor.get('1.0', 'end-1c') if editor else self.window.typed.get()))
        return result[0]

    def test_f3_asks_what_is_playing_on_every_screen(self):
        for context, editor in ((dict(mode='awake'), False), (dict(mode='note'), False), (dict(mode='mailbox'), False),
                                (dict(mode='files'), False), (dict(mode='media', media_navigation=True), False),
                                (dict(mode='local_media'), False), (dict(mode='document_menu'), False),
                                (dict(mode='document', source='doc:1', text='Hello', caret=5), True),
                                (dict(mode='email_draft', source='email:1', text='Hi', caret=2), True),
                                (dict(mode='web', web_browse=True), False), (dict(mode='web', web_app_mode=True), False),
                                (dict(mode='awake', screen_menu=True), False), (dict(mode='awake', confirmation='no'), False),
                                (dict(mode='web', web_private=True, web_field={'label': 'Password'}), False)):
            with self.subTest(context=context):
                self.screen(**context)
                before = self.text(editor)
                self.press('F3', editor=editor)
                if not self.wait(lambda: self.requests()):
                    self.fail('F3 sent nothing. ' + repr(list(self.window.key_trace)[-5:]))
                self.assertEqual(self.requests(), [('keyboard', 'F3')])
                self.assertEqual(self.text(editor), before)

    def test_shift_and_control_f3_are_not_f3(self):
        self.screen(mode='awake')
        for state in (1, 4):
            with self.subTest(state=state):
                self.press('F3', state=state)
                time.sleep(0.2)
                self.assertNotIn(('keyboard', 'F3'), self.requests())


if __name__ == '__main__':
    unittest.main()

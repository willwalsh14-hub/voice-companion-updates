"""Real window keys for the October 2026 changes (Will): Alt+1 to Alt+6 in Email, Control+Z and
Control+Y in documents and drafts, and the hint line that fits each screen.

Like test_alt_keys_window, this adds the Alt bindings Tk makes on Windows when it runs elsewhere."""
import sys
import time
import unittest

from key_state import ALT_STATE
from test_alt_keys_window import AltKeyWindowTests, _window_tests


@_window_tests
class HeaderKeyWindowTests(AltKeyWindowTests):
    # Only the tests below; the Alt menu tests run in their own class.
    test_alt_alone_opens_the_email_menu_and_never_the_system_menu = None
    test_right_alt_is_routed_to_companion_too = None
    test_alt_with_a_key_does_not_open_the_menu = None
    test_alt_where_there_is_no_menu_says_why_and_stays_in_companion = None
    test_alt_letter_shortcuts_reach_companion = None

    def press(self, keysym, state, widget=None):
        def send():
            target = widget() if widget else self.window.typed
            target.focus_force()
            target.event_generate('<KeyPress>', keysym=keysym, state=state)
            target.event_generate('<KeyRelease>', keysym=keysym, state=state)
        self.call(send); time.sleep(.2)

    def test_alt_and_a_number_says_a_header_line_in_the_list_and_in_a_message(self):
        self.screen(mode='mailbox')
        for number in '123456':
            self.window.commands.queue.clear()
            self.keys(('KeyPress', 'Alt_L', 0), ('KeyPress', number, ALT_STATE), ('KeyRelease', number, ALT_STATE),
                      ('KeyRelease', 'Alt_L', ALT_STATE))
            queued = list(self.window.commands.queue)
            self.assertIn(('keyboard', 'Alt+' + number), queued)
            self.assertNotIn(('keyboard', 'Alt'), queued)   # the Alt menu does not open as well
        self.assertEqual(self.given_to_windows(), [])
        self.screen(mode='mailbox', source='mail:1:1', text='Hello from Sam.', readonly=True, email_html=False)
        self.window.commands.queue.clear()
        self.press('4', ALT_STATE, lambda: self.window.editor)
        self.assertIn(('keyboard', 'Alt+4'), list(self.window.commands.queue))

    def test_alt_and_a_number_does_nothing_on_other_screens(self):
        self.screen(mode='awake')
        self.press('1', ALT_STATE)
        self.assertFalse([c for c in self.window.commands.queue if c == ('keyboard', 'Alt+1')])

    def test_control_z_and_control_y_reach_the_document_and_the_draft(self):
        for mode in ('document', 'email_draft'):
            with self.subTest(mode=mode):
                self.screen(mode=mode, source=mode + ':1', text='Hello.', caret=6)
                self.window.commands.queue.clear()
                self.press('z', 4, lambda: self.window.editor)
                self.press('y', 4, lambda: self.window.editor)
                queued = list(self.window.commands.queue)
                self.assertIn(('keyboard', 'Ctrl+Z'), queued)
                self.assertIn(('keyboard', 'Ctrl+Y'), queued)

    def test_the_hint_line_shows_what_the_app_sends(self):
        self.screen(mode='mailbox', display_hint='Email. F1 for help. Enter opens. R replies. N writes new.')
        self.assertEqual(self.call(lambda: self.window.hint_label.cget('text')),
                         'Email. F1 for help. Enter opens. R replies. N writes new.')
        self.screen(mode='awake', display_hint='Microphone off. Use the keys. F1 for help.')
        self.assertEqual(self.call(lambda: self.window.hint_label.cget('text')), 'Microphone off. Use the keys. F1 for help.')


if __name__ == '__main__':
    unittest.main()

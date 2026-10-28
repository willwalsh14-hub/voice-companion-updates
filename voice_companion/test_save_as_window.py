"""F12 in the real window (Will, October 2026).

In an open document F12 is Save As. Insert+F12 and Caps Lock+F12 still say the
time there, and F12 says the time on every other screen. These tests press the
keys in the real Tk window and check what reaches Companion.
"""
import os
import sys
import threading
import time
import unittest


def _display():
    return sys.platform == 'win32' or bool(os.environ.get('DISPLAY'))


def _window_tests(cls):
    if sys.platform == 'win32':
        from real_window_tests import real_window
        return real_window(cls)
    return unittest.skipUnless(_display(), 'needs a display for a real Tk window')(cls)


@_window_tests
class SaveAsKeyWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest('Tk is not installed')
        from app_window import CompanionWindow
        self.window = CompanionWindow('save as key test'); self.window.start()

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

    def screen(self, mode, **context):
        context = dict(dict(mode=mode, source=None, text='', caret=0, ack=0, focus=True), **context)
        self.window.set_context(context)
        end = time.monotonic() + 5
        while self.window.context.get('mode') != mode and time.monotonic() < end: time.sleep(.02)
        if self.window.context.get('mode') != mode: self.fail(repr(self.window.debug_state()))
        target = 'editor' if mode == 'document' else 'typed'
        self.call(lambda: getattr(self.window, target).focus_force())
        time.sleep(.1)
        self.window.commands.queue.clear()
        return target

    def keys(self, target, *events):
        def send():
            widget = getattr(self.window, target)
            for kind, keysym in events:
                widget.event_generate('<' + kind + '>', keysym=keysym)
        self.call(send); time.sleep(.3)

    def keyboard_requests(self):
        return [item[1] for item in list(self.window.commands.queue) if isinstance(item, tuple) and item[0] == 'keyboard']

    def test_f12_in_a_document_is_save_as(self):
        target = self.screen('document', source='1', text='Hello.')
        self.keys(target, ('KeyPress', 'F12'), ('KeyRelease', 'F12'))
        requests = self.keyboard_requests()
        if requests != ['Doc:SaveAs']: self.fail(repr(requests) + ' ' + repr(self.window.debug_state()))

    def test_insert_f12_in_a_document_says_the_time(self):
        target = self.screen('document', source='1', text='Hello.')
        self.keys(target, ('KeyPress', 'Insert'), ('KeyPress', 'F12'), ('KeyRelease', 'F12'), ('KeyRelease', 'Insert'))
        requests = self.keyboard_requests()
        if requests != ['F12']: self.fail(repr(requests) + ' ' + repr(self.window.debug_state()))

    def test_caps_lock_f12_in_a_document_says_the_time(self):
        target = self.screen('document', source='1', text='Hello.')
        self.keys(target, ('KeyPress', 'Caps_Lock'), ('KeyPress', 'F12'), ('KeyRelease', 'F12'), ('KeyRelease', 'Caps_Lock'))
        requests = self.keyboard_requests()
        if requests != ['F12']: self.fail(repr(requests) + ' ' + repr(self.window.debug_state()))

    def test_f12_on_the_main_menu_says_the_time(self):
        target = self.screen('awake')
        self.keys(target, ('KeyPress', 'F12'), ('KeyRelease', 'F12'))
        requests = self.keyboard_requests()
        if requests != ['F12']: self.fail(repr(requests) + ' ' + repr(self.window.debug_state()))


if __name__ == '__main__':
    unittest.main()

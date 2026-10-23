"""Shared helpers for tests that drive the real Voice Companion window.

Every wait and every call on the window thread fails with the window's
debug_state(): whether the thread is alive, when the window last checked its
queues, whether its timer is late, and where the thread is. One failed run on
Windows must be enough to find the cause.

debug_state() is read only after a failure. Reading another thread's frames
while a test passes can free Tk objects on the wrong thread ("Tcl_AsyncDelete").
"""
import threading
import time

WAIT_SECONDS = 5


class RealWindowMixin:
    """For unittest.TestCase classes that keep a started CompanionWindow in self.window."""

    def call(self, function):
        """Run function on the window thread and wait for it. Its exceptions are raised here."""
        done = threading.Event()
        errors = []

        def run():
            try:
                # Geometry is settled before a real key can reach a newly
                # shown widget; synthetic events need the same barrier.
                self.window.root.update_idletasks()
                function()
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        self.window.ui_actions.put(('ui_call', run))
        if not done.wait(WAIT_SECONDS):
            self.fail('The window did not run the request. ' + repr(self.window.debug_state()))
        if errors:
            raise errors[0]

    def wait(self, condition, seconds=WAIT_SECONDS):
        end = time.monotonic() + seconds
        while not condition() and time.monotonic() < end:
            time.sleep(.015)
        if not condition():
            self.fail('The window did not reach the expected state. ' + repr(self.window.debug_state()))

    def close_window(self):
        """For tearDown: close the window on its own thread and make sure the thread ends."""
        if self.window.thread.is_alive():
            self.call(lambda: self.window.root.destroy())
        self.window.thread.join(WAIT_SECONDS)
        if self.window.thread.is_alive():
            self.fail('The window thread did not end. ' + repr(self.window.debug_state()))

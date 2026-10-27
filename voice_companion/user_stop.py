"""Escape, or closing Voice Companion, stops a wait on another program.

A long wait (for Thunderbird, for example) runs on the command thread, so the
window's keys are queued behind it. The window calls request() at once when
Escape or Alt+F4 is pressed; waits that use wait() then stop with Stopped,
and the queued key is handled next. Each new command calls begin_command().
"""
import threading

requested = threading.Event()
_stopped_a_wait = False
SLICE_SECONDS = 0.25


class Stopped(Exception):
    """The user pressed Escape or closed Voice Companion during a wait."""


def request():
    requested.set()


def wait(event, timeout):
    """Like event.wait(timeout), but raises Stopped as soon as a stop is requested."""
    global _stopped_a_wait
    import time
    end = time.monotonic() + timeout
    while True:
        if requested.is_set():
            _stopped_a_wait = True
            raise Stopped()
        left = end - time.monotonic()
        if left <= 0:
            return event.is_set()
        if event.wait(min(SLICE_SECONDS, left)):
            return True


def begin_command():
    """Clear the request for a new command. True when the last stop ended a wait,
    so the Escape that stopped it is not used again to go back."""
    global _stopped_a_wait
    stopped, _stopped_a_wait = _stopped_a_wait, False
    requested.clear()
    return stopped


def check():
    """Between steps of a long job on the command thread: raise Stopped if Escape was pressed."""
    global _stopped_a_wait
    if requested.is_set():
        _stopped_a_wait = True
        raise Stopped()

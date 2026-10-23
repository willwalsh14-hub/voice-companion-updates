"""Escape or closing Voice Companion stops a wait on Thunderbird (Will, 0.2.161).

A long Thunderbird wait ran on the command thread, so Alt+F4 waited behind it
and Voice Companion could not be closed."""
import threading
import time
import unittest
from unittest import mock

import user_stop


class UserStopTests(unittest.TestCase):
    def setUp(self):
        user_stop.begin_command()
        self.addCleanup(user_stop.begin_command)

    def test_a_wait_stops_soon_after_escape(self):
        never = threading.Event()
        threading.Timer(0.1, user_stop.request).start()
        started = time.monotonic()
        with self.assertRaises(user_stop.Stopped):
            user_stop.wait(never, 30)
        self.assertLess(time.monotonic() - started, 2)
        # The Escape that stopped the wait is reported once, so it is not used again to go back.
        self.assertTrue(user_stop.begin_command())
        self.assertFalse(user_stop.begin_command())

    def test_a_wait_without_a_stop_works_as_before(self):
        done = threading.Event(); threading.Timer(0.05, done.set).start()
        self.assertTrue(user_stop.wait(done, 5))
        self.assertFalse(user_stop.wait(threading.Event(), 0.1))
        self.assertFalse(user_stop.begin_command())

    def test_a_new_command_clears_an_old_request(self):
        user_stop.request()
        self.assertFalse(user_stop.begin_command())   # it did not stop a wait
        done = threading.Event(); done.set()
        self.assertTrue(user_stop.wait(done, 1))

    def test_a_thunderbird_request_that_never_answers_stops_with_a_plain_message(self):
        import thunderbird_bridge, thunderbird_mailbox
        from mailbox_access import MailboxError
        relay = mock.Mock(); relay.connected = threading.Event(); relay.connected.set()
        waiter_events = []
        class Pipe:
            def send_bytes(self, data): pass
        bridge = thunderbird_bridge.MailRelay.__new__(thunderbird_bridge.MailRelay)
        bridge.connected = relay.connected; bridge.pending = {}; bridge.ids = iter(range(1, 100))
        bridge.send_lock = threading.Lock(); bridge.pipe = Pipe()
        threading.Timer(0.1, user_stop.request).start()
        with mock.patch.object(thunderbird_bridge, '_RELAY', bridge), mock.patch.object(thunderbird_mailbox, 'STARTER', None):
            with self.assertRaises(MailboxError) as raised:
                thunderbird_mailbox.call('list', folderId='x')
        self.assertEqual(str(raised.exception), 'Stopped waiting for Thunderbird.')
        self.assertEqual(bridge.pending, {})

    def test_the_escape_that_stopped_a_wait_does_not_also_go_back(self):
        import queue, companion
        window = mock.Mock(); window.commands = queue.Queue()
        handled = []
        with mock.patch.object(companion, 'APP_WINDOW', window), \
             mock.patch.object(companion, 'handle_keyboard', lambda command, mode: handled.append(command) or mode), \
             mock.patch.object(companion, 'speak'), mock.patch.object(companion, 'sync_app_context'):
            # A wait was stopped by Escape; then the Escape key and a Down Arrow arrive.
            user_stop.request()
            with self.assertRaises(user_stop.Stopped): user_stop.wait(threading.Event(), 5)
            window.commands.put(('keyboard', 'Escape')); window.commands.put(('keyboard', 'Down'))
            companion.process_keyboard_batch('mailbox')
            # Later, Escape works as usual.
            window.commands.put(('keyboard', 'Escape'))
            companion.process_keyboard_batch('mailbox')
        # Down Arrow, then the later Escape (in the mailbox it returns to the main menu).
        self.assertEqual(handled, ['next', 'main menu'])


if __name__ == '__main__':
    unittest.main()

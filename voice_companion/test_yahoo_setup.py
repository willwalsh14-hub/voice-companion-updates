"""Yahoo account setup: address, then app password, in the app's own speaking text box.

Before, Yahoo setup opened a separate tkinter window from the worker thread while
the app's own window ran on another thread. It froze with no speech, and nothing
could be typed or pasted. Now no separate window is used.
"""
import imaplib
import smtplib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion
import mail_accounts
from mail_accounts import AccountError, yahoo_connect


class FakeStore:
    def __init__(self): self.saved = []
    def save(self, provider, data, select=True): self.saved.append((provider, data))


class FakeSMTP:
    """smtplib.SMTP_SSL as used here: a context manager whose login may refuse."""
    def __init__(self, outcome='ok', seen=None): self.outcome, self.seen = outcome, seen if seen is not None else []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, user, password):
        self.seen.append((user, password))
        if self.outcome == 'refused': raise smtplib.SMTPAuthenticationError(535, b'5.7.0 (#AUTH005) Too many bad auth attempts.')


class FakeIMAP:
    """imaplib.IMAP4_SSL: a refused LOGIN raises IMAP4.error with Yahoo's text."""
    def __init__(self, outcome='ok'): self.outcome = outcome; self.logged_out = False
    def login(self, user, password):
        if self.outcome == 'refused': raise imaplib.IMAP4.error(b'[AUTHENTICATIONFAILED] LOGIN Invalid credentials')
        return ('OK', [b'LOGIN completed'])
    def logout(self): self.logged_out = True; return ('BYE', [b'IMAP4rev1 Server logging out'])


class YahooConnectTests(unittest.TestCase):
    def connect(self, address='sam@yahoo.com', password='abcdefghijklmnop', smtp='ok', imap='ok'):
        store, seen = FakeStore(), []
        imap_server = FakeIMAP(imap)
        def smtp_factory():
            if smtp == 'offline': raise OSError('Network is unreachable')
            return FakeSMTP(smtp, seen)
        result = yahoo_connect(store, address, password, smtp_factory, lambda: imap_server)
        return result, store, seen, imap_server

    def test_good_app_password_is_checked_and_saved(self):
        result, store, seen, imap = self.connect()
        self.assertEqual(result, 'sam@yahoo.com')
        self.assertEqual(store.saved, [('yahoo', {'address': 'sam@yahoo.com', 'app_password': 'abcdefghijklmnop'})])
        self.assertTrue(imap.logged_out)

    def test_pasted_password_with_spaces_or_dashes_works(self):
        for pasted in ('abcd efgh ijkl mnop', 'abcd-efgh-ijkl-mnop', ' abcdefghijklmnop\r\n'):
            with self.subTest(pasted=pasted):
                _, store, seen, _ = self.connect(password=pasted)
                self.assertEqual(seen[0][1], 'abcdefghijklmnop')

    def test_spoken_address_is_understood(self):
        result, *_ = self.connect(address='Sam at Yahoo dot com.')
        self.assertEqual(result, 'sam@yahoo.com')

    def test_refused_password_is_explained_and_nothing_saved(self):
        for smtp, imap in (('refused', 'ok'), ('ok', 'refused')):
            with self.subTest(smtp=smtp, imap=imap):
                with self.assertRaises(AccountError) as raised:
                    self.connect(smtp=smtp, imap=imap)
                self.assertIn('did not accept that app password', str(raised.exception))
                self.assertNotIn('abcdefghijklmnop', str(raised.exception))

    def test_no_internet_is_explained(self):
        with self.assertRaises(AccountError) as raised:
            self.connect(smtp='offline')
        self.assertIn('could not be reached', str(raised.exception))

    def test_bad_address_and_empty_password(self):
        with self.assertRaisesRegex(AccountError, 'not a complete email address'):
            self.connect(address='sam')
        with self.assertRaisesRegex(AccountError, 'app password is needed'):
            self.connect(password='  ')

    def test_old_window_is_never_opened(self):
        with patch.dict(sys.modules, {'tkinter': None}):
            with self.assertRaises(AccountError):
                mail_accounts.connect('yahoo', tempfile.mkdtemp(), graphical=True)


class YahooSetupConversationTests(unittest.TestCase):
    def setUp(self):
        self.spoken = []
        for name, value in (('TEXT_MODE', True), ('APP_WINDOW', None), ('ACCOUNT_ENTRY', None), ('account_setup', False),
                            ('APP', Path(tempfile.mkdtemp()))):
            patcher = patch.object(companion, name, value); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(companion, 'speak', self.spoken.append); patcher.start(); self.addCleanup(patcher.stop)

    def say(self, text):
        return companion.handle(text, 'awake')

    def start(self):
        self.say('add account'); self.say('Yahoo.')
        self.assertEqual(companion.ACCOUNT_ENTRY, {'provider': 'yahoo', 'step': 'address'})
        self.assertIn('app password', self.spoken[-1])

    def test_whole_setup_by_voice_and_paste(self):
        self.start()
        self.say('sam at yahoo dot com')
        self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'password')
        self.assertIn('sam@yahoo.com', self.spoken[-1])
        context = companion.app_context('awake')
        self.assertTrue(context['account_private'])
        with patch.object(companion, 'yahoo_connect', return_value='sam@yahoo.com') as connect:
            companion.finish_account_secret('abcd efgh ijkl mnop', 'awake')
        self.assertEqual(connect.call_args.args[1:], ('sam@yahoo.com', 'abcd efgh ijkl mnop'))
        self.assertIn('Checking the app password', self.spoken[-2])
        self.assertIn('connected and selected', self.spoken[-1])
        self.assertIsNone(companion.ACCOUNT_ENTRY)

    def test_spoken_password_is_refused_and_never_repeated(self):
        self.start(); self.say('sam at yahoo dot com')
        self.say('wxyz secret words')
        self.assertIn('For privacy', self.spoken[-1])
        self.assertNotIn('secret', ' '.join(self.spoken))
        self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'password')

    def test_refused_password_keeps_setup_open(self):
        self.start(); self.say('sam@yahoo.com')
        with patch.object(companion, 'yahoo_connect', side_effect=AccountError('Yahoo did not accept that app password.')):
            companion.finish_account_secret('wrongwrongwrongw', 'awake')
        self.assertIn('did not accept', self.spoken[-1])
        self.assertIn('again', self.spoken[-1])
        self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'password')

    def test_change_address_and_cancel(self):
        self.start(); self.say('sam@yahoo.com')
        self.say('Change address.')
        self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'address')
        self.say('Cancel.')
        self.assertIsNone(companion.ACCOUNT_ENTRY)
        self.assertIn('No account was saved', self.spoken[-1])

    def test_bad_address_is_asked_again(self):
        self.start(); self.say('sam')
        self.assertIn('not a complete email address', self.spoken[-1])
        self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'address')

    def test_keys_do_not_reach_the_menu_behind(self):
        self.start()
        self.assertEqual(companion.navigation_key('Escape', 'awake'), 'cancel account setup')
        for key in ('Enter', 'Down', 'Up', 'Tab'):
            self.assertEqual(companion.navigation_key(key, 'awake'), 'account entry empty')
        self.say(companion.navigation_key('Enter', 'awake'))
        self.assertIn('The box is empty', self.spoken[-1])
        self.assertFalse(companion.app_context('awake')['menu_letters'])


class PrivateBoxTests(unittest.TestCase):
    def window(self):
        import queue, app_window
        win = app_window.CompanionWindow.__new__(app_window.CompanionWindow)
        win.context = {'account_entry': 'password', 'account_private': True}
        win.commands = queue.Queue(); win.notices = []; win.feedback = win.notices.append
        return win

    def test_enter_sends_the_password_to_setup_only(self):
        win = self.window()
        self.assertTrue(win.submit_entry('abcdefghijklmnop'))
        self.assertEqual(win.commands.get_nowait(), ('account_secret', 'abcdefghijklmnop'))

    def test_typing_and_paste_are_spoken_as_hidden(self):
        win = self.window()
        win.report_entry_key('', 'a', 0, 1, 'a', 'a')
        win.report_entry_key('a', 'abcd efgh ijkl mnop', 1, 19, 'v', '\x16')
        win.report_entry_key('abc', 'ab', 3, 2, 'BackSpace')
        self.assertEqual(win.notices, ['Hidden character.', '18 hidden characters pasted.', 'Deleted. 2 hidden characters left.'])
        self.assertFalse(any('abc' in notice for notice in win.notices))


if __name__ == '__main__':
    unittest.main()

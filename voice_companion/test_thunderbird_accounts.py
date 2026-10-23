"""Thunderbird accounts: told apart from direct ones, added from Voice Companion, started by themselves.

Before 0.2.160:
- "Gmail, will@gmail.com" could be a direct Gmail connection or the same
  address in Thunderbird; switching, the send review and "list email accounts"
  could not tell them apart ("Switched to will@gmail.com").
- Adding another Thunderbird account needed a helper in Thunderbird.
- A closed Thunderbird gave "Thunderbird is not connected. Open Thunderbird".
- A computer without Thunderbird got a website to visit.

The add flow runs end to end: the real add-on (background.js) in Node against
the simulated Thunderbird, the real host code and relay, real account files.
Windows (registry, programs, installer) is the FakeSystem from
test_thunderbird_setup.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import account_picker
import companion
import mail_accounts
import regular_browser
import thunderbird_accounts
import thunderbird_bridge
import thunderbird_mailbox
import thunderbird_setup
from browser_bridge_host import read_message
from mail_accounts import ProtectedStore
from mailbox_access import MailboxError
from test_core_email_fixes import CompanionTestCase
from test_email_accounts import AccountScreenTestCase, plain_store
from test_thunderbird_setup import ACCOUNT_PREFS, INSTALLED, FakeRelay, FakeSystem, registered

HERE = Path(__file__).parent
GMAIL, BIRD_GMAIL = ('gmail', 'will@gmail.com'), ('thunderbird', 'will@gmail.com')


# ---- 1. Telling Thunderbird accounts apart -----------------------------------

class WordingTests(unittest.TestCase):
    def test_kind_then_address_everywhere(self):
        self.assertEqual(account_picker.label(BIRD_GMAIL), 'Thunderbird, will@gmail.com')
        self.assertEqual(account_picker.label(GMAIL, GMAIL), 'Gmail, will@gmail.com, current')

    def test_spoken_kind_and_address_in_any_order(self):
        for said, expected in [('Thunderbird, will at gmail dot com.', ('thunderbird', 'will at gmail dot com')),
                               ('will@gmail.com through Thunderbird', ('thunderbird', 'will@gmail.com')),
                               ('the Thunderbird account will@gmail.com', ('thunderbird', 'will@gmail.com')),
                               ('will@gmail.com in Mozilla Thunderbird.', ('thunderbird', 'will@gmail.com')),
                               ('Gmail will@gmail.com', ('gmail', 'will@gmail.com')),
                               ('Google mail, will@gmail.com', ('gmail', 'will@gmail.com')),
                               ('will at gmail dot com', (None, 'will at gmail dot com')),
                               ('sam at yahoo dot com', (None, 'sam at yahoo dot com'))]:
            with self.subTest(said):
                self.assertEqual(account_picker.spoken_account(said), expected)

    def test_choice_with_the_same_address_both_ways(self):
        accounts = [GMAIL, BIRD_GMAIL, ('yahoo', 'sam@yahoo.com')]
        self.assertEqual(account_picker.choice('Thunderbird, will at gmail dot com.', accounts), 1)
        self.assertEqual(account_picker.choice('will@gmail.com through Thunderbird', accounts), 1)
        self.assertEqual(account_picker.choice('Gmail, will@gmail.com', accounts), 0)
        self.assertEqual(account_picker.choice('Thunderbird', accounts), 1)
        found = account_picker.choice('will@gmail.com', accounts)
        self.assertEqual(found, ('several', ''))
        self.assertEqual(account_picker.several('', accounts),
                         'More than one account has that address. Say the account number, '
                         'or say the kind of account first, such as Thunderbird, will@gmail.com.')
        self.assertIsNone(account_picker.choice('Outlook, will@gmail.com', accounts))


class SameAddressTwoWaysTests(AccountScreenTestCase):
    ACCOUNTS = (GMAIL, BIRD_GMAIL, ('yahoo', 'sam@yahoo.com'))

    def test_account_list_says_the_kind_first(self):
        companion.handle('switch account', 'awake')
        self.assertTrue(self.last().startswith('Choose an account to open. Gmail, will@gmail.com, current, 1 of 3.'), self.last())
        self.key('End', 'awake')
        self.assertEqual(self.last(), 'Thunderbird, will@gmail.com, 3 of 3.')
        self.assertEqual(self.key('Enter', 'awake'), 'mailbox')
        self.assertEqual(self.store.selected(), BIRD_GMAIL)
        self.assertTrue(self.said[-1].startswith('Switched to Thunderbird, will@gmail.com.'), self.said[-1])

    def test_voice_choice_in_the_list(self):
        companion.handle('switch account', 'awake')
        companion.handle('Will at Gmail dot com.', 'awake')
        self.assertIn('say the kind of account first, such as Thunderbird, will@gmail.com', self.last())
        self.assertIsNotNone(companion.ACCOUNT_PICKER)
        companion.handle('Thunderbird, will at gmail dot com.', 'awake')
        self.assertEqual(self.store.selected(), BIRD_GMAIL)

    def test_switch_to_names_both_ways_and_accepts_the_kind(self):
        companion.handle('switch to will@gmail.com', 'awake')
        self.assertEqual(self.last(), 'That address is connected 2 ways. Say switch to Gmail, will@gmail.com, '
                                      'or switch to Thunderbird, will@gmail.com.')
        self.assertEqual(self.store.selected(), GMAIL)
        companion.handle('Switch to Thunderbird, will at gmail dot com.', 'awake')
        self.assertEqual(self.store.selected(), BIRD_GMAIL)
        self.assertEqual(self.last(), 'Switched to Thunderbird, will@gmail.com.')
        companion.handle('switch to will@gmail.com through gmail', 'awake')
        self.assertEqual(self.store.selected(), GMAIL)
        companion.handle('use thunderbird', 'awake')
        self.assertEqual(self.store.selected(), BIRD_GMAIL)

    def test_list_email_accounts_are_two_items_that_each_switch(self):
        companion.handle('list email accounts', 'awake')
        items = companion.NAMED_LIST['items']
        self.assertEqual([label for label, _ in items], ['Gmail, will@gmail.com, current', 'Yahoo, sam@yahoo.com',
                                                         'Thunderbird, will@gmail.com'])
        companion.handle(items[2][1], 'awake')
        self.assertEqual(self.store.selected(), BIRD_GMAIL)
        companion.handle(items[0][1], 'awake')
        self.assertEqual(self.store.selected(), GMAIL)

    def test_send_review_says_which_way_it_sends(self):
        self.store.select(*BIRD_GMAIL)
        draft = self.draft_for('thunderbird')
        companion.handle('send email', 'email_draft')
        self.assertTrue(self.last().startswith('Review before sending. From Thunderbird, will@gmail.com. To friend@example.com'),
                        self.last())
        self.assertIs(companion.email_draft, draft)

    def draft_for(self, provider):
        from email_draft import VoiceEmail
        from document_editor import Paragraph
        draft = VoiceEmail(self.folder / 'Email Drafts', recipient='friend@example.com', subject='Hi', provider=provider,
                           compose_step='body')
        draft.paragraphs = [Paragraph('See you soon.')]
        companion.email_draft = draft
        return draft


# ---- 2. Adding Thunderbird accounts from Voice Companion ---------------------

class WatchTests(unittest.TestCase):
    """The watch alone, with a scripted Thunderbird and a simulated clock."""

    def make(self, answers, limit=60, reminder=30):
        self.now = 0.0
        calls = []

        def request(method, params=None):
            calls.append(method)
            answer = answers.get(method)
            return answer() if callable(answer) else answer

        def sleep(seconds):
            self.now += seconds
        watch = thunderbird_accounts.Watch(request, 'new@example.org', [{'id': 'a1', 'identities': [{'id': 'i1', 'email': 'me@x.org'}]}],
                                           clock=lambda: self.now, sleep=sleep, poll=5, reminder=reminder, limit=limit)
        return watch, calls

    def events(self, watch):
        found = []
        while not watch.events.empty():
            found.append(watch.events.get_nowait())
        return found

    def test_new_identity_is_reported_once(self):
        accounts = [[{'id': 'a1', 'identities': [{'id': 'i1', 'email': 'me@x.org'}]}]] * 3 + \
                   [[{'id': 'a1', 'identities': [{'id': 'i1', 'email': 'me@x.org'}]},
                     {'id': 'a2', 'identities': [{'id': 'i2', 'email': 'new@example.org'}]}]]
        watch, calls = self.make({'accounts': lambda: accounts.pop(0), 'takeSignIn': {'url': '', 'where': ''}})
        watch.run()
        [(event, found)] = self.events(watch)
        self.assertEqual(event, 'ready')
        self.assertEqual([i['email'] for _, i in thunderbird_accounts.identities(found)], ['me@x.org', 'new@example.org'])

    def test_reminder_then_gives_up_and_survives_a_closed_thunderbird(self):
        def closed():
            raise thunderbird_bridge.ThunderbirdUnavailable('gone')
        watch, _ = self.make({'accounts': closed, 'takeSignIn': closed})
        watch.run()
        self.assertEqual([event for event, _ in self.events(watch)], ['reminder', 'gave_up'])

    def test_sign_in_page_and_page_after_continue(self):
        signs = [{'url': 'https://accounts.google.com/o/oauth2/auth?x=1', 'where': 'browser'}] + [{'url': '', 'where': ''}] * 99
        pages = [{'open': True, 'title': 'Looking up settings'}, {'open': True, 'title': 'Settings found'}]
        watch, _ = self.make({'accounts': [], 'takeSignIn': lambda: signs.pop(0), 'setupPage': lambda: pages.pop(0) if pages else {}},
                             limit=12)
        watch.after_press({'open': True, 'title': 'Looking up settings'})
        watch.run()
        found = self.events(watch)
        self.assertEqual(found[0], ('sign_in', signs and {'url': 'https://accounts.google.com/o/oauth2/auth?x=1', 'where': 'browser'}))
        self.assertEqual(found[1], ('page', {'open': True, 'title': 'Settings found'}))
        self.assertEqual(found[-1][0], 'gave_up')

    def test_page_words(self):
        self.assertEqual(thunderbird_accounts.page_words(
            {'open': True, 'title': 'Set up your existing email address', 'notes': ['Configuration found in the Mozilla ISP database.'],
             'button': 'Done', 'canContinue': True, 'needsPassword': True}),
            'Thunderbird shows: Set up your existing email address. Configuration found in the Mozilla ISP database. '
            'It needs the password for this account. Say continue in Thunderbird to choose Done.')
        self.assertIn('not open', thunderbird_accounts.page_words({'open': False}))
        self.assertEqual(thunderbird_accounts.page_words({'open': True, 'button': 'Continue', 'canContinue': False}),
                         'Continue is not available yet.')

    def test_sign_in_words_by_provider_and_version(self):
        self.assertIn('Google asks you to sign in. The sign-in page opens in your web browser',
                      thunderbird_accounts.sign_in_words('a@gmail.com', 153))
        self.assertIn('in a Thunderbird window', thunderbird_accounts.sign_in_words('a@outlook.com', 128))
        self.assertIn('password', thunderbird_accounts.sign_in_words('a@example.org', 153))
        self.assertIn('Yahoo signs in only in Thunderbird', thunderbird_accounts.sign_in_in_thunderbird('a@yahoo.com'))


@unittest.skipUnless(shutil.which('node'), 'Node.js runs the simulated Thunderbird')
class AddThunderbirdAccountTests(CompanionTestCase):
    """End to end: Voice Companion, relay, host, real add-on, simulated Thunderbird."""

    ADD_MS = '300'

    def setUp(self):
        super().setUp()
        if os.name == 'nt':
            endpoint, family = None, 'AF_PIPE'
        else:
            endpoint, family = str(self.folder / 'mail.sock'), 'AF_UNIX'
        self.relay = thunderbird_bridge.MailRelay(self.folder, endpoint, family)
        self.addCleanup(self.relay.close)
        for patcher in [patch.object(thunderbird_bridge, '_RELAY', self.relay),
                        patch.object(regular_browser, 'data_folder', lambda: self.folder),
                        patch.object(companion, 'THUNDERBIRD_WATCH', None), patch.object(companion, 'THUNDERBIRD_SETUP', None),
                        patch.object(companion, 'THUNDERBIRD_SIGN_IN', None), patch.object(companion, 'ACCOUNT_ENTRY', None),
                        patch.object(companion, 'account_setup', False), patch.object(companion, 'web_session', None),
                        patch.object(companion, 'sync_app_context', lambda *a, **k: None),
                        patch.object(thunderbird_mailbox, 'STARTER', None),
                        patch.object(thunderbird_setup.WindowsSystem, 'registry', lambda self, *a: None),
                        patch('document_editor.copy_to_clipboard', lambda text: None)] + plain_store():
            patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(companion.stop_thunderbird_watch)
        environment = dict(os.environ, FAKE_THUNDERBIRD_ADD_MS=self.ADD_MS)
        self.thunderbird = subprocess.Popen(['node', str(HERE / 'thunderbird_mail_extension' / 'fake-thunderbird.cjs')],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        self.addCleanup(self.stop_thunderbird)
        first = read_message(self.thunderbird.stdout)
        threading.Thread(target=thunderbird_bridge.native_stream,
                         args=(first, self.thunderbird.stdout, self.thunderbird.stdin, endpoint, family), daemon=True).start()
        self.assertTrue(self.relay.connected.wait(10), 'Thunderbird did not connect')
        self.store = ProtectedStore(self.folder)

    def stop_thunderbird(self):
        try: self.thunderbird.stdin.close()
        except OSError: pass
        try: self.thunderbird.wait(10)
        except subprocess.TimeoutExpired: self.thunderbird.kill()
        for stream in (self.thunderbird.stdout, self.thunderbird.stderr):
            stream.close()

    def until_ready(self, mode='awake', seconds=15):
        """The app's main loop, for a while: what the watch reports is spoken on this thread."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and companion.THUNDERBIRD_WATCH is not None:
            mode = companion.poll_thunderbird_watch(mode)
            time.sleep(0.05)
        return mode

    def test_add_another_account_by_voice_then_another(self):
        self.store.save('thunderbird', {'address': 'me@gmail.com', 'account_id': 'account1', 'identity_id': 'id1'})
        companion.handle('Add Thunderbird account.', 'awake')
        self.assertEqual(self.said[-1], thunderbird_accounts.ADDRESS_PROMPT)
        self.assertEqual(companion.ACCOUNT_ENTRY['provider'], 'thunderbird')
        self.assertEqual(companion.app_context('awake')['account_label'], thunderbird_accounts.ADDRESS_LABEL)
        self.assertFalse(companion.app_context('awake')['account_private'])
        companion.handle('Pat at example dot org.', 'awake')
        self.assertIsNone(companion.ACCOUNT_ENTRY)
        self.assertTrue(self.said[-1].startswith('Thunderbird has opened its add account page for pat@example.org, '
                                                 'with your name and address filled in. Thunderbird shows: '
                                                 'Set up your existing email address. Say continue in Thunderbird to choose Continue.'),
                        self.said[-1])
        self.until_ready()
        self.assertEqual(self.said[-1], 'Thunderbird finished adding pat@example.org. Thunderbird, pat@example.org is connected '
                                        'and selected. Say check email to open it. To add another, say add Thunderbird account.')
        self.assertEqual(self.store.selected(), ('thunderbird', 'pat@example.org'))
        # A second one right away, without restarting anything.
        companion.handle('add another Thunderbird account', 'awake')
        companion.handle('lee@example.net', 'awake')
        self.until_ready()
        self.assertIn('Thunderbird, lee@example.net is connected and selected', self.said[-1])
        accounts = [a for a in self.store.accounts() if a[0] == 'thunderbird']
        self.assertEqual(sorted(accounts), [('thunderbird', 'lee@example.net'), ('thunderbird', 'me@gmail.com'),
                                            ('thunderbird', 'pat@example.org')])
        # Each one is its own Thunderbird account: its own inbox.
        for address in ('pat@example.org', 'lee@example.net'):
            self.store.select('thunderbird', address)
            saved = self.store.load('thunderbird')
            self.assertNotEqual(saved['account_id'], 'account1')
            self.assertEqual(thunderbird_mailbox.ThunderbirdMailbox(address, saved).folders(), [('Inbox', saved['account_id'] + '://INBOX')])

    def test_google_sign_in_opens_in_voice_companions_browser(self):
        opened = []

        class Browser:
            snapshot = True
            page_revision = 0
            pending = submit_pending = None
            def __init__(self, *args, **kwargs):
                pass
            def open(self, url):
                opened.append(url); self.page_revision += 1
                return 'Sign in - Google Accounts.'
            def choose_browser(self, name):
                opened.append('choose ' + name)
        with patch.object(companion, 'WebSession', Browser), \
             patch.object(companion, 'speak_web_result', lambda text, revision: self.said.append(text)):
            companion.handle('add Thunderbird account', 'awake')
            companion.handle('will@gmail.com', 'awake')
            deadline = time.monotonic() + 10
            mode = 'awake'
            while not opened and time.monotonic() < deadline:
                mode = companion.poll_thunderbird_watch(mode)
                time.sleep(0.05)
            self.assertEqual(mode, 'web')
            self.assertTrue(opened[0].startswith('https://accounts.google.com/o/oauth2/auth?login_hint=will%40gmail.com'))
            self.assertIn('Google sign-in page is opening in Voice Companion\'s browser', ' '.join(self.said))
            # The other browser, if asked.
            companion.handle('open sign-in page in Chrome', 'awake')
            self.assertEqual(opened[1:], ['choose chrome', opened[0]])
            self.until_ready('web')
        self.assertIn('Thunderbird, will@gmail.com is connected and selected', self.said[-1])
        self.assertIsNone(companion.THUNDERBIRD_SIGN_IN)

    def test_address_thunderbird_already_has_is_connected_at_once(self):
        # Addresses added in Thunderbird earlier are connected before the question, and said.
        companion.handle('add thunderbird account', 'awake')
        self.assertEqual(self.said[-1], 'Thunderbird already had me@gmail.com. It is now connected too. ' + thunderbird_accounts.ADDRESS_PROMPT)
        companion.handle('me@gmail.com', 'awake')
        self.assertEqual(self.said[-1], 'Thunderbird, me@gmail.com is already connected. Say switch account to use it.')
        self.assertIsNone(companion.THUNDERBIRD_WATCH)
        # Removed from Voice Companion meanwhile: connected again at once, nothing opened in Thunderbird.
        self.store.remove('thunderbird', 'me@gmail.com')
        companion.add_thunderbird_address('me@gmail.com', None, 'awake')
        self.assertEqual(self.said[-1], 'Thunderbird already has me@gmail.com. Thunderbird, me@gmail.com is now connected '
                                        'and selected. Say check email to open it.')
        self.assertEqual(self.store.selected(), ('thunderbird', 'me@gmail.com'))
        self.assertIsNone(companion.THUNDERBIRD_WATCH)

    def test_connect_flow_offers_to_add_another_when_all_are_connected(self):
        self.store.save('thunderbird', {'address': 'me@gmail.com', 'account_id': 'account1', 'identity_id': 'id1'})
        companion.handle('add account', 'awake')
        companion.handle('Thunderbird.', 'awake')
        self.assertEqual(self.said[-1], 'Your Thunderbird account is already connected: Thunderbird, me@gmail.com. '
                                        'Add another email account to Thunderbird? Say yes or no.')
        companion.handle('Yes.', 'awake')
        self.assertEqual(self.said[-1], thunderbird_accounts.ADDRESS_PROMPT)
        companion.handle('cancel', 'awake')
        self.assertEqual(self.said[-1], thunderbird_accounts.CANCELED)

    def test_first_connect_names_the_kind_and_the_new_command(self):
        companion.handle('connect thunderbird', 'awake')
        self.assertEqual(self.said[-1], 'Thunderbird, me@gmail.com is connected and selected. Say check email to open it. '
                                        'To add another email account to Thunderbird, say add Thunderbird account.')

    def test_name_is_asked_when_thunderbird_has_none(self):
        self.relay.request('accounts')   # the simulated identity has the name "Me"; take it away
        with patch.object(thunderbird_mailbox, 'accounts', lambda: [{'id': 'account1', 'identities': [
                {'id': 'id1', 'email': 'me@gmail.com', 'name': ''}]}]):
            companion.handle('add thunderbird account', 'awake')
            companion.handle('pat@example.org', 'awake')
        self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'name')
        self.assertTrue(self.said[-1].startswith('Adding pat@example.org. What name should people see'), self.said[-1])
        self.assertEqual(companion.app_context('awake')['account_label'], thunderbird_accounts.NAME_LABEL)
        companion.handle('Pat Smith.', 'awake')
        self.assertIsNone(companion.ACCOUNT_ENTRY)
        self.assertIn('with your name and address filled in', self.said[-1])

    def test_continue_read_and_password_never_spoken(self):
        companion.handle('add thunderbird account', 'awake')
        companion.handle('pat@example.org', 'awake')
        page = {'open': True, 'title': 'Set up your existing email address', 'notes': [], 'button': 'Done',
                'canContinue': True, 'needsPassword': True}
        original = self.relay.request
        sent = []

        def request(method, params=None, timeout=60):
            if method == 'setupPage':
                return dict(page)
            if method == 'fillPassword':
                sent.append(len(params['password'])); page['needsPassword'] = False
                return {'filled': True}
            return original(method, params, timeout)
        with patch.object(self.relay, 'request', request):
            companion.handle('What does Thunderbird say?', 'awake')
            self.assertIn('It needs the password for this account. Say continue in Thunderbird to choose Done.', ' '.join(self.said))
            self.assertEqual(companion.ACCOUNT_ENTRY['step'], 'password')
            self.assertTrue(companion.app_context('awake')['account_private'])
            companion.handle('my secret words', 'awake')       # said aloud: refused, never repeated
            self.assertNotIn('my secret words', ' '.join(self.said))
            self.assertIn('type or paste the password instead of saying it', self.said[-1])
            companion.finish_account_secret('pa55-word', 'awake')
            self.assertEqual(sent, [9])
            self.assertEqual(self.said[-1], thunderbird_accounts.PASSWORD_SENT)
            self.assertNotIn('pa55-word', ' '.join(self.said))
            companion.handle('continue in thunderbird', 'awake')
            self.assertEqual(self.said[-1], 'Choosing Done in Thunderbird. Voice Companion reads the page when it changes.')
        self.until_ready()

    def test_stop_waiting(self):
        companion.handle('add thunderbird account', 'awake')
        with patch.object(thunderbird_accounts.Watch, 'run', lambda self: None):
            companion.handle('pat@example.org', 'awake')
        self.assertIsNotNone(companion.THUNDERBIRD_WATCH)
        companion.handle('stop waiting for Thunderbird', 'awake')
        self.assertIsNone(companion.THUNDERBIRD_WATCH)
        self.assertEqual(self.said[-1], thunderbird_accounts.STOPPED_WAITING)
        companion.handle('stop waiting for Thunderbird', 'awake')
        self.assertEqual(self.said[-1], thunderbird_accounts.NOT_WAITING)
        companion.handle('continue in thunderbird', 'awake')
        self.assertEqual(self.said[-1], thunderbird_accounts.NOT_ADDING)

    def test_an_older_add_on_must_be_updated_first(self):
        with patch.object(self.relay, 'version', '1.0.0'):
            companion.handle('add thunderbird account', 'awake')
        self.assertEqual(self.said[-1], thunderbird_accounts.NEEDS_UPDATE)
        self.assertIsNone(companion.ACCOUNT_ENTRY)


class AddCommandRoutingTests(CompanionTestCase):
    def test_add_phrases_start_the_add_flow_and_sentences_stay_text(self):
        started = []
        with patch.object(companion, 'start_thunderbird_add', lambda mode: started.append(mode) or mode):
            for phrase in ('add Thunderbird account', 'Add another Thunderbird account.', 'add an email account to Thunderbird',
                           'new Thunderbird account'):
                companion.handle(phrase, 'awake')
            self.assertEqual(len(started), 4)
            document = self.open_document()
            companion.handle('Add my email to Thunderbird.', 'document')
            self.assertEqual(len(started), 4)
            self.assertIn('Add my email to Thunderbird', companion.document_text(document))

    def test_sign_in_words_mean_nothing_special_when_nothing_waits(self):
        with patch.object(companion, 'THUNDERBIRD_WATCH', None), patch.object(companion, 'THUNDERBIRD_SIGN_IN', None):
            self.assertIsNone(companion.thunderbird_setup_command('open sign-in page', 'awake'))
            self.assertIsNone(companion.thunderbird_setup_command('check email', 'awake'))
            self.assertEqual(companion.thunderbird_setup_command('continue in thunderbird', 'awake'), 'awake')
            self.assertEqual(self.said[-1], thunderbird_accounts.NOT_ADDING)

    def test_sign_in_page_never_interrupts_writing(self):
        watch = thunderbird_accounts.Watch(lambda *a, **k: None, 'will@gmail.com', [])
        watch.events.put(('sign_in', {'url': 'https://accounts.google.com/o/oauth2/auth', 'where': 'browser'}))
        opened = []
        self.open_document()
        with patch.object(companion, 'THUNDERBIRD_WATCH', {'watch': watch, 'address': 'will@gmail.com'}), \
             patch.object(companion, 'THUNDERBIRD_SIGN_IN', None), patch.object(companion, 'ACCOUNT_ENTRY', None), \
             patch.object(companion, 'open_thunderbird_sign_in', lambda mode, browser=None: opened.append(mode) or 'web'):
            self.assertEqual(companion.poll_thunderbird_watch('document'), 'document')
            self.assertEqual(self.said[-1], thunderbird_accounts.SIGN_IN_READY)
            self.assertEqual(opened, [])
            self.assertEqual(companion.handle('open sign-in page', 'document'), 'web')
            self.assertEqual(opened, ['document'])

    def test_show_thunderbird_any_time(self):
        class Relay:
            connected = threading.Event()
            def request(self, method, params=None, timeout=60):
                return {'shown': method == 'showSetup'}
        relay = Relay(); relay.connected.set()
        with patch.object(thunderbird_bridge, '_RELAY', relay), patch.object(companion, 'THUNDERBIRD_WATCH', None):
            companion.handle('Show Thunderbird.', 'awake')
        self.assertEqual(self.said[-1], 'Thunderbird is in front now. Press Alt plus Tab to come back to Voice Companion.')

    def test_menu_item(self):
        from screen_menus import MENUS
        accounts = dict(MENUS['mailbox'][1])['Accounts']
        self.assertIn(('Add Thunderbird account', '', 'add thunderbird account'), accounts)


# ---- 3. Thunderbird starts by itself -----------------------------------------

class StarterCase(CompanionTestCase):
    def setUp(self):
        # The startup cadence (2 seconds, then every 15) is checked here; user_stop.wait's
        # quarter-second slices for Escape are covered by test_user_stop.
        import user_stop
        stop_patch = patch.object(user_stop, 'wait', lambda event, timeout: event.wait(timeout))
        stop_patch.start(); self.addCleanup(stop_patch.stop)
        super().setUp()
        self.home = self.folder / 'home'
        self.package = self.folder / 'mail.xpi'
        self.package.write_bytes(b'PK add-on')
        for patcher in [patch.object(companion, 'addon_package', lambda: self.package),
                        patch.object(companion, 'THUNDERBIRD_SETUP', None), patch.object(companion, 'CONFIRM_CHOICE', 'no')]:
            patcher.start(); self.addCleanup(patcher.stop)

    def ready_system(self, running=False, addon=True):
        root = self.home / 'Roaming' / 'Thunderbird'
        profile = root / 'Profiles' / 'abc.default-release'
        profile.mkdir(parents=True)
        (profile / 'prefs.js').write_text(ACCOUNT_PREFS, encoding='utf-8')
        (root / 'profiles.ini').write_text('[Profile0]\nName=default\nIsRelative=1\nPath=Profiles/abc.default-release\nDefault=1\n',
                                           encoding='utf-8')
        if addon:
            (profile / 'extensions').mkdir()
            thunderbird_setup.addon_file(profile).write_bytes(b'PK old add-on')
            (profile / 'extensions.json').write_text(json.dumps({'addons': [{'id': thunderbird_bridge.ADDON_ID, 'active': True}]}))
        return FakeSystem(self.home, registered(), {INSTALLED}, running=running), profile


class RelayWithRequests(FakeRelay):
    def __init__(self, connect_after=None):
        super().__init__(connect_after)
        self.requests = []

    def request(self, method, params=None, timeout=60):
        self.requests.append(method)
        return {'minimized': 1} if method == 'minimize' else [{'id': 'account1', 'identities': [{'id': 'id1', 'email': 'me@x.org'}]}]


class StarterTests(StarterCase):
    def start(self, system, relay):
        focus = []
        window = type('Window', (), {'reclaim_focus': lambda self: focus.append(True)})()
        with patch.object(companion, 'mail_relay', lambda: relay), patch.object(companion, 'APP_WINDOW', window):
            companion.thunderbird_starter(system)
        return focus

    def test_quick_start_says_nothing_and_keeps_the_keyboard(self):
        system, profile = self.ready_system()
        relay = RelayWithRequests(connect_after=0)
        focus = self.start(system, relay)
        self.assertEqual(system.background, [INSTALLED])
        self.assertEqual(system.launched, [])            # never the normal, focus-taking start
        self.assertEqual(self.said, [])
        self.assertEqual(relay.requests, ['minimize'])
        self.assertEqual(focus, [True])
        # An older add-on copy was replaced before Thunderbird started.
        self.assertEqual(thunderbird_setup.addon_file(profile).read_bytes(), b'PK add-on')

    def test_slow_start_says_one_line_then_every_15_seconds(self):
        system, _ = self.ready_system()
        relay = RelayWithRequests(connect_after=3)
        self.start(system, relay)
        self.assertEqual(relay.waits, [2, 15, 15, 15])
        self.assertEqual(self.said, ['Starting Thunderbird.', 'Still starting Thunderbird.', 'Still starting Thunderbird.'])

    def test_no_connection_after_a_minute_says_what_to_do(self):
        system, _ = self.ready_system()
        with self.assertRaises(MailboxError) as raised:
            self.start(system, RelayWithRequests())
        self.assertEqual(str(raised.exception), thunderbird_setup.AUTO_NO_CONNECTION)
        self.assertNotIn('Open Thunderbird', str(raised.exception))

    def test_already_running_is_not_started_again(self):
        system, _ = self.ready_system(running=True)
        relay = RelayWithRequests(connect_after=0)
        focus = self.start(system, relay)
        self.assertEqual(system.__dict__.get('background'), None)
        self.assertEqual(relay.requests, [])
        self.assertEqual(focus, [])

    def test_missing_add_on_asks_to_install_it(self):
        system, _ = self.ready_system(addon=False)
        flow = thunderbird_setup.Setup(system, self.package, lambda: 'x', lambda: RelayWithRequests(), self.said.append)
        with patch.object(companion, 'new_thunderbird_setup', lambda adding=False: flow), self.assertRaises(MailboxError) as raised:
            self.start(system, RelayWithRequests())
        self.assertEqual(str(raised.exception), thunderbird_setup.ADDON_MISSING)
        self.assertEqual(companion.THUNDERBIRD_SETUP['question'], 'install')

    def test_not_installed_asks_to_install_thunderbird(self):
        system = FakeSystem(self.home)
        flow = thunderbird_setup.Setup(system, self.package, lambda: 'x', lambda: RelayWithRequests(), self.said.append)
        with patch.object(companion, 'new_thunderbird_setup', lambda adding=False: flow), self.assertRaises(MailboxError) as raised:
            self.start(system, RelayWithRequests())
        self.assertIn('Install Thunderbird? Say yes or no.', str(raised.exception))
        self.assertEqual(companion.THUNDERBIRD_SETUP['question'], 'install_program')

    def test_mail_command_goes_on_by_itself_once_connected(self):
        relay = RelayWithRequests()
        calls = []

        def starter():
            calls.append('start'); relay.connected.set()
        with patch.object(thunderbird_bridge, '_RELAY', relay), patch.object(thunderbird_mailbox, 'STARTER', starter):
            found = thunderbird_mailbox.accounts()
        self.assertEqual(calls, ['start'])
        self.assertEqual(found[0]['identities'][0]['email'], 'me@x.org')

    def test_thunderbird_closed_mid_request_is_started_and_asked_again(self):
        relay = RelayWithRequests(connect_after=None)
        relay.connected.set()
        answers = [thunderbird_bridge.ThunderbirdUnavailable('gone'), [{'id': 'a', 'identities': []}]]

        def request(method, params=None, timeout=60):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                relay.connected.clear(); raise answer
            return answer
        relay.request = request
        with patch.object(thunderbird_bridge, '_RELAY', relay), \
             patch.object(thunderbird_mailbox, 'STARTER', lambda: relay.connected.set()):
            self.assertEqual(thunderbird_mailbox.accounts(), [{'id': 'a', 'identities': []}])

    def test_startup_starts_thunderbird_quietly_only_for_a_thunderbird_account(self):
        launched = []
        with patch.object(thunderbird_setup, 'start_in_background', lambda *a, **k: launched.append(True) or False):
            for item in plain_store():
                item.start(); self.addCleanup(item.stop)
            store = ProtectedStore(self.folder)
            store.save('gmail', {'address': 'a@gmail.com'})
            self.assertIsNone(companion.start_thunderbird_quietly())
            store.save('thunderbird', {'address': 'b@x.org', 'account_id': 'a', 'identity_id': 'i'})
            thread = companion.start_thunderbird_quietly()
            thread.join(5)
        self.assertEqual(launched, [True])
        self.assertEqual(self.said, [])


# ---- 4. Installing Thunderbird with one yes ----------------------------------

class InstallTests(StarterCase):
    def setup_flow(self, system, relay):
        return thunderbird_setup.Setup(system, self.package, lambda: 'x', lambda: relay, self.said.append,
                                       ready=lambda setup: thunderbird_setup.Step('Ready to add an account.', None), adding=True)

    def first_start_makes_a_profile(self, system):
        def make():
            root = self.home / 'Roaming' / 'Thunderbird'
            (root / 'Profiles' / 'new.default-release').mkdir(parents=True)
            (root / 'Profiles' / 'new.default-release' / 'prefs.js').write_text('// new\n', encoding='utf-8')
            (root / 'profiles.ini').write_text('[Profile0]\nName=default-release\nIsRelative=1\n'
                                               'Path=Profiles/new.default-release\nDefault=1\n', encoding='utf-8')
        system.profile_after_launch = make

    def test_one_yes_installs_with_winget_then_adds_the_connection_and_starts(self):
        system = FakeSystem(self.home)
        system.install_polls = 40
        self.first_start_makes_a_profile(system)
        relay = RelayWithRequests(connect_after=0)
        flow = self.setup_flow(system, relay)
        step = flow.start()
        self.assertEqual(step.question, 'install_program')
        step = flow.answer('install_program', True)
        self.assertEqual(system.commands[0], ['winget', 'install', '--id', 'Mozilla.Thunderbird', '-e', '--silent',
                                              '--accept-package-agreements', '--accept-source-agreements'])
        self.assertEqual(self.said[0], thunderbird_setup.INSTALLING)
        self.assertEqual(self.said.count(thunderbird_setup.STILL_INSTALLING), 2)   # 40 seconds: at 15 and 30
        self.assertIn('Thunderbird is installed. Starting Thunderbird for the first time.', self.said)
        self.assertEqual(system.closes, 1)                    # closed once, after it made its profile
        profile = self.home / 'Roaming' / 'Thunderbird' / 'Profiles' / 'new.default-release'
        self.assertTrue(thunderbird_setup.addon_file(profile).is_file())
        self.assertIn('extensions.autoDisableScopes', (profile / 'user.js').read_text(encoding='utf-8'))
        self.assertEqual(system.background, [INSTALLED, INSTALLED])
        self.assertEqual(step.message, 'Ready to add an account.')

    def test_without_winget_mozillas_signed_installer_runs(self):
        system = FakeSystem(self.home)
        system.winget = False
        self.first_start_makes_a_profile(system)
        flow = self.setup_flow(system, RelayWithRequests(connect_after=0))
        flow.start()
        step = flow.answer('install_program', True)
        self.assertEqual(system.downloads, [thunderbird_setup.INSTALLER_URL])
        self.assertEqual(system.checked, ['Thunderbird Setup.exe'])
        self.assertEqual(system.commands[0][1:], ['/S'])
        self.assertFalse(Path(system.commands[0][0]).exists(), 'the installer is deleted afterwards')
        self.assertEqual(step.message, 'Ready to add an account.')

    def test_an_installer_not_signed_by_mozilla_never_runs(self):
        for signature in (('NotSigned', ''), ('Valid', 'CN=Someone Else, O=Someone Else, C=US'),
                          ('HashMismatch', 'CN=Mozilla Corporation, O=Mozilla Corporation, C=US')):
            with self.subTest(signature):
                system = FakeSystem(self.home)
                system.winget, system.signature = False, signature
                flow = self.setup_flow(system, RelayWithRequests(connect_after=0))
                flow.start()
                step = flow.answer('install_program', True)
                self.assertEqual(system.__dict__.get('commands'), None)
                self.assertEqual(step.message, thunderbird_setup.INSTALL_FAILED)

    def test_a_stuck_installer_is_stopped(self):
        system = FakeSystem(self.home)
        system.install_polls = 10 ** 6
        flow = self.setup_flow(system, RelayWithRequests(connect_after=0))
        flow.start()
        step = flow.answer('install_program', True)
        self.assertEqual(step.message, thunderbird_setup.INSTALL_FAILED)
        # winget, then Mozilla's installer, each stopped at its time limit.
        self.assertLessEqual(system.now, 2 * thunderbird_setup.INSTALL_SECONDS + 4)
        self.assertEqual(len(system.commands), 2)

    def test_closed_thunderbird_starts_without_a_question_when_adding(self):
        system, profile = self.ready_system()
        relay = RelayWithRequests(connect_after=0)
        step = self.setup_flow(system, relay).start()
        self.assertEqual(step.message, 'Ready to add an account.')
        self.assertEqual(system.background, [INSTALLED])
        self.assertEqual(thunderbird_setup.addon_file(profile).read_bytes(), b'PK add-on')   # newer copy in place first

    def test_installed_but_never_opened_is_started_once_when_adding(self):
        system = FakeSystem(self.home, registered(), {INSTALLED})
        self.first_start_makes_a_profile(system)
        step = self.setup_flow(system, RelayWithRequests(connect_after=0)).start()
        self.assertEqual(step.message, 'Ready to add an account.')
        self.assertEqual(system.closes, 1)


if __name__ == '__main__':
    unittest.main()

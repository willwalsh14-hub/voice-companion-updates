"""Add another email account to Thunderbird from Voice Companion, and say when it is ready.

Thunderbird add-ons cannot create email accounts (its accounts API only lists
them and reports changes), and only Thunderbird may run its own sign-in. So,
all from Voice Companion's own screen:

  1. Voice Companion asks for the address (and, the first time, the name
     people see) in its own speaking text box.
  2. If Thunderbird already has that address, it is connected at once.
  3. Otherwise the Voice Companion connection opens Thunderbird's own
     "add email account" page in the background, fills in the name and
     address, and reads the page aloud. "Continue in Thunderbird" presses its
     main button; a password typed privately in Voice Companion goes into its
     password box.
  4. Google and Microsoft sign in through a web page (Thunderbird 151 and
     newer). The connection hands that page to Voice Companion, which opens it
     in its own browser, embedded Edge or Chrome. The answer goes back to
     Thunderbird on this computer. Yahoo and AOL sign in only in Thunderbird's
     own window, which Voice Companion cannot read.
  5. A Watch asks Thunderbird for its accounts every few seconds. When the new
     address appears, Voice Companion saves it, selects it and says so.

Passwords are never spoken, kept or logged.
"""
import queue
import threading
import time

from account_picker import label

POLL_SECONDS = 2
PAGE_SECONDS = 20     # after "continue", the page is read when it changes, or after this long
REMINDER_SECONDS = 5 * 60
WAIT_SECONDS = 15 * 60
# Thunderbird opens these providers' own sign-in pages (OAuth) instead of asking for a password.
SIGN_IN_PROVIDERS = {
    'gmail.com': 'Google', 'googlemail.com': 'Google',
    'outlook.com': 'Microsoft', 'hotmail.com': 'Microsoft', 'live.com': 'Microsoft', 'msn.com': 'Microsoft',
    'yahoo.com': 'Yahoo', 'ymail.com': 'Yahoo', 'rocketmail.com': 'Yahoo',
    'aol.com': 'AOL', 'aim.com': 'AOL',
}
EXTERNAL_SIGN_IN_VERSION = 153   # Thunderbird opens sign-in pages in the web browser from this version on

NAME_PROMPT = ('What name should people see on email you send? Type or say it, such as Will Walsh, then press Enter. '
               'Say cancel to stop.')
NAME_LABEL = 'Your name, as people see it on email; type, then press Enter. Escape cancels:'
PASSWORD_PROMPT = ('Thunderbird needs the password for this email account. Type or paste it here, then press Enter. '
                   'It goes straight to Thunderbird and is not spoken. For Yahoo, AOL or iCloud, use an app password. '
                   'Say cancel to skip.')
PASSWORD_LABEL = 'Email account password for Thunderbird (private); paste or type, then press Enter. Escape cancels:'
PASSWORD_SENT = 'The password is in Thunderbird. Continuing.'
PASSWORD_NOT_SENT = 'Thunderbird has no password box open right now. Nothing was typed. Say what does Thunderbird say to hear the page.'
CONTINUE_COMMANDS = ('continue in thunderbird', 'thunderbird continue', 'continue thunderbird', 'continue adding account',
                     'continue adding the account', 'continue adding thunderbird account', 'press continue in thunderbird')
READ_COMMANDS = ('what does thunderbird say', 'read thunderbird', 'read the thunderbird page', 'read thunderbird page',
                 'thunderbird status', 'what is thunderbird doing')
SHOW_COMMANDS = ('show thunderbird', 'go to thunderbird', 'bring thunderbird forward', 'show the thunderbird window')
PASSWORD_COMMANDS = ('enter thunderbird password', 'type thunderbird password', 'thunderbird password',
                     'enter the thunderbird password', 'enter password for thunderbird')
NOT_ADDING = 'Voice Companion is not adding a Thunderbird account right now. Say add Thunderbird account to start.'
NO_SIGN_IN = 'No sign-in page from Thunderbird is waiting.'
SIGN_IN_READY = 'The sign-in page for your new Thunderbird account is ready. Say open sign-in page when you want it.'
ADDRESS_PROMPT = 'Type or say the email address to add to Thunderbird, then press Enter. Say cancel to stop.'
ADDRESS_LABEL = 'Email address to add to Thunderbird; type, then press Enter. Escape cancels:'
ADDRESS_AGAIN = 'That is not a complete email address. Type or say it again, such as sam at gmail dot com. Say cancel to stop.'
CANCELED = 'Adding a Thunderbird account is canceled. Nothing was changed.'
BUSY = 'Voice Companion is waiting for an email address to add to Thunderbird. Type it and press Enter, or press Escape to cancel.'
EMPTY = 'The box is empty. Type the email address to add to Thunderbird, then press Enter. Press Escape to cancel.'
STOPPED_WAITING = ('Voice Companion stopped waiting for Thunderbird. If you finish adding the account in Thunderbird, '
                   'say add account and choose Thunderbird to connect it.')
NOT_WAITING = 'Voice Companion is not waiting for a Thunderbird account.'
NEEDS_UPDATE = ('The Voice Companion connection in Thunderbird must be updated first. Close Thunderbird, '
                'then say add Thunderbird account. Voice Companion updates the connection and starts Thunderbird again.')
STOP_WAITING_COMMANDS = ('stop waiting for thunderbird', 'cancel thunderbird account', 'cancel adding thunderbird account',
                         'stop adding thunderbird account', 'cancel adding the thunderbird account')
ADD_COMMANDS = ('add thunderbird account', 'add a thunderbird account', 'add another thunderbird account',
                'add new thunderbird account', 'add a new thunderbird account', 'new thunderbird account',
                'add thunderbird', 'add thunderbird email', 'add thunderbird mail', 'add thunderbird email account',
                'add account to thunderbird', 'add an account to thunderbird', 'add another account to thunderbird',
                'add email account to thunderbird', 'add an email account to thunderbird',
                'add another email account to thunderbird', 'add email to thunderbird', 'add my email to thunderbird')


def identities(found):
    """(account, identity) pairs with an address, in Thunderbird's order."""
    return [(account, identity) for account in found or [] for identity in account.get('identities') or []
            if identity.get('email')]


def _account_number(account):
    digits = ''.join(ch for ch in str(account.get('id', '')) if ch.isdigit())
    return int(digits) if digits else 0


def refresh_saved(store, found):
    """A saved address whose Thunderbird identity is gone (the account was added again, so it
    has new ids) is pointed at the current one. Return the addresses that changed.

    Will, 0.2.163: Voice Companion kept the old ids, and sent through the old account's
    outgoing server after Thunderbird's own one was fixed."""
    current = {}
    for account, identity in identities(found):
        current.setdefault(identity['email'].casefold(), []).append((account, identity))
    changed = []
    for provider, address in store.accounts():
        if provider != 'thunderbird' or address.casefold() not in current:
            continue
        path = store._account_path('thunderbird', address)
        if not path.exists():
            continue
        try:
            data = store._unseal(path)
        except Exception:
            continue
        choices = current[address.casefold()]
        if any(identity['id'] == data.get('identity_id') for _, identity in choices):
            continue
        account, identity = max(choices, key=lambda pair: _account_number(pair[0]))
        data.update(account_id=account['id'], identity_id=identity['id'], name=account.get('name', ''))
        store.save('thunderbird', data, select=False)
        changed.append(address)
    return changed


def save_identities(store, found, prefer=None, select=True):
    """Save Thunderbird addresses Voice Companion does not have yet. Return the new addresses.

    Already saved addresses keep their place, and so does the selection, unless
    select is True: then the preferred address (or the first new one) is selected.
    A saved address whose identity Thunderbird no longer has is updated (refresh_saved)."""
    refresh_saved(store, found)
    saved = {a.casefold() for p, a in store.accounts() if p == 'thunderbird'}
    added = []
    for account, identity in identities(found):
        address = identity['email']
        if address.casefold() in saved:
            continue
        store.save('thunderbird', {'address': address, 'account_id': account['id'],
                                   'identity_id': identity['id'], 'name': account.get('name', '')}, select=False)
        saved.add(address.casefold()); added.append(address)
    if select and added:
        chosen = next((a for a in added if prefer and a.casefold() == prefer.casefold()), added[0])
        store.select('thunderbird', chosen)
    return added


def sign_in_words(address, version=None):
    """What Thunderbird asks for after Continue, in a short sentence or two."""
    domain = address.rsplit('@', 1)[-1].casefold()
    company = SIGN_IN_PROVIDERS.get(domain)
    if not company:
        return 'Then Thunderbird asks for the password of this email account. Type it in Thunderbird, not in Voice Companion.'
    where = ('in your web browser' if version is not None and version >= EXTERNAL_SIGN_IN_VERSION else
             'in a Thunderbird window' if version is not None else 'in your web browser or in a Thunderbird window')
    return 'Then ' + company + ' asks you to sign in. The sign-in page opens ' + where + '.'


def company(address):
    return SIGN_IN_PROVIDERS.get(address.rsplit('@', 1)[-1].casefold(), '')


def opened_message(address, opened, prefilled, copied, version=None):
    if opened and prefilled:
        # Voice Companion reads the page next and can press its buttons.
        return 'Thunderbird has opened its add account page for ' + address + ', with your name and address filled in.'
    if opened:
        start = 'Thunderbird is open to add ' + address + '. '
        if copied:
            fill = 'The address is copied. Say show Thunderbird, then paste it in the email address box with Control V. '
        else:
            fill = 'Say show Thunderbird, then type the address in the email address box. '
        steps = 'Type your name if that box is empty, then choose Continue. '
    else:
        start = 'Thunderbird could not open its add account page by itself. '
        fill = ('The address is copied. ' if copied else '')
        steps = ('In Thunderbird, press Alt, open the File menu, choose New, then Existing Mail Account. '
                 'Fill in your name and the address, then choose Continue. ')
    return (start + fill + steps + sign_in_words(address, version) +
            ' Voice Companion tells you when the account is ready.')


def ready_message(added, selected):
    """The account (or accounts) Thunderbird just finished adding."""
    if not added:
        return ''
    names = ' and '.join(added)
    text = 'Thunderbird finished adding ' + names + '. '
    if selected:
        return text + label(('thunderbird', selected)) + ' is connected and selected. Say check email to open it.'
    return text + ('It is' if len(added) == 1 else 'They are') + ' connected. Say switch account to use ' + \
        ('it.' if len(added) == 1 else 'one.')


class Watch:
    """Asks Thunderbird for its accounts until a new address appears, then reports it once.

    Runs on its own thread; events() is read on the app's main loop, which
    does all saving and speaking. request is the relay's request function."""

    def __init__(self, request, address, known, clock=time.monotonic, sleep=None, poll=POLL_SECONDS,
                 reminder=REMINDER_SECONDS, limit=WAIT_SECONDS):
        self.request, self.address = request, address
        self.known = {identity.get('id') for _, identity in identities(known)}
        self.clock, self.poll, self.reminder, self.limit = clock, poll, reminder, limit
        self.stopped = threading.Event()
        self.sleep = sleep or self.stopped.wait
        self.events = queue.Queue()
        self.thread = None
        self.lock = threading.Lock()
        self.pressed = None     # (page before "continue", when): read the page when it changes

    def start(self):
        self.thread = threading.Thread(target=self.run, name='Thunderbird account watch', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()

    def after_press(self, before):
        with self.lock:
            self.pressed = (before, self.clock())

    def _ask(self, method, params=None):
        try:
            return self.request(method, params or {})
        except Exception:
            return None

    def check_sign_in(self):
        found = self._ask('takeSignIn')
        if isinstance(found, dict) and (found.get('url') or found.get('where') == 'thunderbird'):
            self.events.put(('sign_in', found))

    def check_page(self):
        with self.lock:
            pressed = self.pressed
        if pressed is None:
            return
        before, when = pressed
        page = self._ask('setupPage')
        if page is None:
            return
        if page != before or self.clock() - when >= PAGE_SECONDS:
            with self.lock:
                self.pressed = None
            self.events.put(('page', page))

    def check(self):
        """One look at Thunderbird's accounts: the found list when a new address is there, else None."""
        try:
            found = self.request('accounts')
        except Exception:
            return None    # Thunderbird closed or busy: keep waiting, it may come back.
        new = [identity for _, identity in identities(found) if identity.get('id') not in self.known]
        return found if new else None

    def run(self):
        started = self.clock()
        reminded = False
        while not self.stopped.is_set():
            found = self.check()
            if found is not None:
                self.events.put(('ready', found))
                return
            self.check_sign_in()
            self.check_page()
            waited = self.clock() - started
            if waited >= self.limit:
                self.events.put(('gave_up', None))
                return
            if not reminded and waited >= self.reminder:
                reminded = True
                self.events.put(('reminder', None))
            self.sleep(self.poll)


def page_words(page):
    """Thunderbird's add account page, in a few short sentences."""
    if not page or not page.get('open'):
        return ('Thunderbird\'s add account page is not open. If you closed it, say add Thunderbird account to start again.')
    words = []
    if page.get('title'):
        words.append('Thunderbird shows: ' + page['title'].rstrip('.') + '.')
    for note in page.get('notes') or []:
        words.append(note.rstrip('.') + '.')
    if page.get('needsPassword'):
        words.append('It needs the password for this account.')
    if page.get('canContinue') and page.get('button'):
        words.append('Say continue in Thunderbird to choose ' + page['button'] + '.')
    elif page.get('button'):
        words.append(page['button'] + ' is not available yet.')
    return ' '.join(words) or 'Thunderbird is working. Say what does Thunderbird say to check again.'


def sign_in_opening(address):
    who = company(address) or 'The'
    return (who + ' sign-in page is opening in Voice Companion\'s browser. Sign in there. '
            'Thunderbird gets the answer by itself, and Voice Companion tells you when the account is ready.')


def sign_in_in_thunderbird(address):
    who = company(address) or 'This provider'
    return (who + ' signs in only in Thunderbird\'s own window, not in Voice Companion. Say show Thunderbird to bring it '
            'forward, then sign in with a screen reader or a helper. Voice Companion tells you when the account is ready.')


def reminder_message(address):
    return ('Still waiting for Thunderbird to add ' + address + '. '
            'Say stop waiting for Thunderbird to stop.')


def gave_up_message(address):
    return ('Thunderbird has not added ' + address + ' after ' + str(WAIT_SECONDS // 60) + ' minutes. ' + STOPPED_WAITING)

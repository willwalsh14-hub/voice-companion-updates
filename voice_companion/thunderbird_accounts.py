"""Add another email account to Thunderbird from Voice Companion, and say when it is ready.

Thunderbird add-ons cannot create email accounts (its accounts API only lists
them and reports changes), and only Thunderbird may run its own sign-in. So:

  1. Voice Companion asks for the address in its own speaking text box.
  2. If Thunderbird already has that address, it is connected at once.
  3. Otherwise the Voice Companion connection opens Thunderbird's own
     "add email account" page and fills in the address where it can.
     Thunderbird looks up the settings, and asks for the sign-in: a password
     typed in Thunderbird, or the provider's sign-in page (in the web browser
     from Thunderbird 153, in a Thunderbird window before that).
  4. A Watch asks Thunderbird for its accounts every few seconds. When the new
     address appears, Voice Companion saves it, selects it and says so.

No password passes through Voice Companion. Nothing here is logged.
"""
import queue
import threading
import time

from account_picker import label

POLL_SECONDS = 5
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


def save_identities(store, found, prefer=None, select=True):
    """Save Thunderbird addresses Voice Companion does not have yet. Return the new addresses.

    Already saved addresses are left alone, and so is the selection, unless
    select is True: then the preferred address (or the first new one) is selected."""
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


def opened_message(address, opened, prefilled, copied, version=None):
    if opened:
        start = 'Thunderbird is open to add ' + address + '. '
        if prefilled:
            fill = 'The address is filled in. '
        elif copied:
            fill = 'The address is copied. In Thunderbird, paste it in the email address box with Control V. '
        else:
            fill = 'In Thunderbird, type the address in the email address box. '
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

    def start(self):
        self.thread = threading.Thread(target=self.run, name='Thunderbird account watch', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()

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
            waited = self.clock() - started
            if waited >= self.limit:
                self.events.put(('gave_up', None))
                return
            if not reminded and waited >= self.reminder:
                reminded = True
                self.events.put(('reminder', None))
            self.sleep(self.poll)


def reminder_message(address):
    return ('Still waiting for Thunderbird to add ' + address + '. '
            'Say stop waiting for Thunderbird to stop.')


def gave_up_message(address):
    return ('Thunderbird has not added ' + address + ' after ' + str(WAIT_SECONDS // 60) + ' minutes. ' + STOPPED_WAITING)

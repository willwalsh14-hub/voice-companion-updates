"""Which calendars or address books the Calendar and Contacts screens show.

With two or more email accounts in Thunderbird (Gmail and Outlook, say), a person
can show one calendar or address book, every one of one account, or all of them.
With one account, none of this is offered or spoken: everything is shown, as before.

A source is a dict from the add-on: {'id', 'name', 'account', 'readOnly'}, and for
calendars 'type' ('storage' is a calendar kept on this computer). 'account' is the
email address it belongs to, or '' (on this computer, a subscription, or not known).
An add-on from before accounts (1.4 and older) sends no 'account' at all; then the
choice is never offered.

A choice is 'all', 'account:<email>' or 'source:<id>'. The choice for each screen is
kept in calendar-contacts-view.json in the app folder, written atomically, with the
words that name it, so a source that is gone can be named once when all are shown again.
"""
import json
import re
from pathlib import Path

from storage import write_text_atomic

FILE = 'calendar-contacts-view.json'
ALL = 'all'
# The words for each screen: one, many, and the kind of thing in it.
NOUNS = {'calendar': ('calendar', 'calendars'), 'contacts': ('address book', 'address books')}
# Provider names people say, from the first part of the email domain.
PROVIDERS = {'gmail': 'Gmail', 'googlemail': 'Gmail', 'outlook': 'Outlook', 'hotmail': 'Outlook', 'live': 'Outlook',
             'msn': 'Outlook', 'yahoo': 'Yahoo', 'ymail': 'Yahoo', 'aol': 'AOL', 'icloud': 'iCloud', 'me': 'iCloud',
             'mac': 'iCloud', 'comcast': 'Comcast', 'proton': 'Proton', 'protonmail': 'Proton'}


def provider(account):
    """'Gmail' for me@gmail.com, 'Outlook' for vet@hotmail.co.uk, '' for a work or unknown address."""
    domain = str(account or '').rpartition('@')[2].casefold()
    return PROVIDERS.get(domain.split('.')[0], '') if domain else ''


def account_words(account):
    """'Gmail, me@gmail.com', or the address alone when the provider has no plain name."""
    name = provider(account)
    return (name + ', ' if name else '') + account


def spoken(text):
    """Words for matching what was said: lower case, no punctuation, and no 'at' or 'dot', so
    'me at gmail dot com', 'me@gmail.com' and 'Me@Gmail.com.' are the same."""
    words = re.sub(r'[^\w\s]', ' ', str(text).casefold()).split()
    return ' '.join(word for word in words if word not in ('at', 'dot'))


class Sources:
    """The calendars or address books on a screen, and the choices made from them."""

    def __init__(self, kind, items):
        self.kind = kind
        self.one, self.many = NOUNS[kind]
        self.items = [dict(item) for item in items if isinstance(item, dict) and str(item.get('id') or '')]
        for item in self.items:
            item['id'] = str(item['id'])
            item['account'] = str(item.get('account') or '').casefold().strip()
        self.known = any('account' in item for item in items if isinstance(item, dict))

    # ---- What there is -----------------------------------------------------------

    def accounts(self):
        """The email accounts, in the order Thunderbird lists their first calendar or book."""
        found = []
        for item in self.items:
            if item['account'] and item['account'] not in found:
                found.append(item['account'])
        return found

    def active(self):
        """True when a choice is offered: two or more email accounts. Calendars and books kept
        on this computer do not count, so one Gmail account with Thunderbird's Home calendar
        shows everything, as before."""
        return self.known and len(self.accounts()) >= 2

    def of_account(self, account):
        return [item for item in self.items if item['account'] == account]

    def find(self, source_id):
        return next((item for item in self.items if item['id'] == str(source_id)), None)

    # ---- Names ---------------------------------------------------------------------

    def label(self, item):
        """'Calendar, Outlook, vet@outlook.com'; 'Family, Gmail, me@gmail.com'; 'Home, on this computer';
        'Holidays'. A calendar named by its own address is called Calendar (or Address book)."""
        name = ' '.join(str(item.get('name') or '').split())
        account = item['account']
        if account:
            if not name or name.casefold() == account:
                name = self.one.capitalize()
            return name + ', ' + account_words(account)
        if item.get('type') == 'storage':
            return (name or self.one.capitalize()) + ', on this computer'
        return name or self.one.capitalize()

    def short(self, item):
        """The name said in each event line: the name alone when no other has it, else with the provider
        or address, so two calendars called Calendar are told apart."""
        name = ' '.join(str(item.get('name') or '').split())
        same = [other for other in self.items if ' '.join(str(other.get('name') or '').split()).casefold() == name.casefold()]
        if len(same) < 2 or not item['account']:
            return name
        word = provider(item['account'])
        if word and sum(provider(other['account']) == word for other in same) == 1:
            return name + ', ' + word
        return name + ', ' + item['account']

    def choices(self):
        """[(choice, words)]: All first; then each account, with its own sources after it when it has
        more than one; then the calendars or books that belong to no account."""
        rows = [(ALL, 'All ' + self.many)]
        for account in self.accounts():
            mine = self.of_account(account)
            if len(mine) > 1:
                rows.append(('account:' + account, account_words(account) + ', all ' + self.many))
            rows.extend(('source:' + item['id'], self.label(item)) for item in mine)
        rows.extend(('source:' + item['id'], self.label(item)) for item in self.items if not item['account'])
        return rows

    def has(self, choice):
        return any(value == choice for value, _ in self.choices())

    def words(self, choice):
        """What is shown, as words: 'All calendars', 'Gmail, me@gmail.com, all calendars', or a label."""
        return next((words for value, words in self.choices() if value == choice), 'All ' + self.many)

    def ids(self, choice):
        """The ids shown for a choice, or None for all."""
        if choice.startswith('account:'):
            return {item['id'] for item in self.of_account(choice[8:])}
        if choice.startswith('source:') and self.find(choice[7:]):
            return {choice[7:]}
        return None

    def shown(self, choice):
        ids = self.ids(choice)
        return [item for item in self.items if ids is None or item['id'] in ids]

    # ---- Matching what was said ------------------------------------------------------

    def match(self, said):
        """The choices that spoken words name: 'Gmail', 'Outlook', 'me at gmail dot com', 'Family',
        'all'. An account's name goes to the account (or its only source); a name to a source."""
        wanted = spoken(said)
        if not wanted:
            return []
        if wanted in ('all', 'every', 'everything', 'all of them', 'every one', 'all ' + spoken(self.many),
                      'every ' + spoken(self.one), 'all my ' + spoken(self.many)):
            return [ALL]
        keys = []          # (choice, words that name it)
        for account in self.accounts():
            mine = self.of_account(account)
            target = 'account:' + account if len(mine) > 1 else 'source:' + mine[0]['id']
            for words in (provider(account), account, account.partition('@')[0], account_words(account)):
                if words:
                    keys.append((target, spoken(words)))
        for item in self.items:
            for words in (item.get('name'), self.label(item), self.short(item)):
                if words:
                    keys.append(('source:' + item['id'], spoken(words)))
        found = []
        for choice, words in keys:
            if words == wanted and choice not in found:
                found.append(choice)
        if found:
            # A name and a provider can both fit ("Outlook"); the account wins over its own calendar.
            accounts = [choice for choice in found if choice.startswith('account:')]
            return accounts if accounts and len(found) > 1 and all(
                choice.startswith('account:') or self._in(choice, accounts) for choice in found) else found
        # Fewer words than a full name: "Family" for "Family calendar", "Outlook calendar" for "Calendar, Outlook".
        loose = []
        for choice, words in keys:
            if (' ' + wanted + ' ') in (' ' + words + ' ') and choice not in loose:
                loose.append(choice)
        return loose

    def _in(self, choice, accounts):
        item = self.find(choice[7:]) if choice.startswith('source:') else None
        return bool(item and 'account:' + item['account'] in accounts)


# ---- The choice kept between sessions ------------------------------------------------

def _read(folder):
    try:
        value = json.loads((Path(folder) / FILE).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def saved(folder, kind):
    """{'choice', 'words'} kept for this screen, or {}."""
    value = _read(folder).get(kind)
    return value if isinstance(value, dict) and isinstance(value.get('choice'), str) else {}


def remember(folder, kind, choice, words):
    """Keep the choice for next time. False when it could not be written."""
    value = _read(folder)
    value[kind] = {'choice': choice, 'words': words}
    try:
        write_text_atomic(Path(folder) / FILE, json.dumps(value, indent=2))
        return True
    except OSError:
        return False


def resolve(folder, kind, sources):
    """(choice, notice) when a screen opens. The kept choice, or All with a notice once when its
    calendar or address book is gone. With one account, always All and no notice."""
    if not sources.active():
        return ALL, ''
    kept = saved(folder, kind)
    choice = kept.get('choice') or ALL
    if choice == ALL or sources.has(choice):
        return choice, ''
    one, many = NOUNS[kind]
    words = str(kept.get('words') or '').strip() or 'The ' + one + ' you chose'
    remember(folder, kind, ALL, 'All ' + many)
    return ALL, words + ' is no longer in Thunderbird, so all ' + many + ' are shown.'

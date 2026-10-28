"""All of a person's contacts in one list: Thunderbird's address books and the local address book.

Thunderbird syncs Google and other address books by itself; they are read through
the add-on (thunderbird_pim). The local address book (address_book.py) keeps
contacts saved on this computer. Both are merged, sorted by name, and a contact
saved in both places is said once (matched by email address).

The merged list is kept in memory for a few minutes, so "email John" in a draft
does not ask Thunderbird every time. Nothing is written to disk here.
"""
import re
import time
from dataclasses import dataclass, field

import thunderbird_pim
from address_book import AddressBook

KEEP_SECONDS = 300
LOCAL_ID = '@local'     # the contacts saved on this computer, as one address book (see ContactBook.view)
# Last list read from Thunderbird: {'at': monotonic time, 'contacts': [Contact], 'books': [...]}.
CACHE = {'at': None, 'contacts': [], 'books': []}


@dataclass
class Contact:
    name: str
    emails: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    source: str = 'local'           # 'thunderbird' or 'local'
    id: str = ''                    # Thunderbird's contact id, or the local name key
    book: str = ''                  # the address book's name in Thunderbird
    book_id: str = ''               # the address book's id in Thunderbird
    read_only: bool = False

    @property
    def key(self):
        return self.source + ':' + (self.id or self.name.casefold())

    def label(self):
        """What Up and Down say: name, email, phone."""
        parts = [self.name or 'No name']
        if self.emails:
            parts.append(self.emails[0])
        if self.phones:
            parts.append(self.phones[0])
        return ', '.join(parts)

    def where(self):
        if self.source == 'local':
            return 'Saved on this computer'
        return 'Thunderbird address book ' + self.book if self.book else 'Thunderbird'


def words(text):
    """A name as speech gives it, for matching: lower case, no punctuation, single spaces."""
    return ' '.join(re.sub(r'[^\w\s@.-]', ' ', str(text).casefold()).replace('.', ' ').split())


def from_thunderbird(item):
    def strings(values):
        return [str(v).strip() for v in (values or []) if isinstance(v, str) and str(v).strip()][:10]
    emails = strings(item.get('emails'))
    name = str(item.get('name') or '').strip() or (emails[0] if emails else '')
    return Contact(name=name[:200], emails=emails, phones=strings(item.get('phones')), source='thunderbird',
                   id=str(item.get('id') or ''), book=str(item.get('book') or ''), book_id=str(item.get('bookId') or ''),
                   read_only=bool(item.get('readOnly')))


def from_local(entry):
    address = str(entry.get('address') or '').strip()
    phone = str(entry.get('phone') or '').strip()
    name = str(entry.get('name') or '').strip()
    return Contact(name=name, emails=[address] if address else [], phones=[phone] if phone else [],
                   source='local', id=name.casefold())


def merge(thunderbird, local):
    """One list, sorted by name. Thunderbird first; a duplicate (same email) is left out.
    Collected Addresses (Thunderbird's list of everyone you wrote to) comes last, so a real
    address book wins."""
    ordered = sorted(thunderbird, key=lambda c: 'collected' in c.book.casefold())
    seen, merged, plain = set(), [], set()
    for contact in ordered + list(local):
        emails = {e.casefold() for e in contact.emails}
        if emails and emails <= seen:
            continue
        if not emails:
            same = (contact.name.casefold(), tuple(contact.phones))
            if same in plain:
                continue
            plain.add(same)
        seen |= emails
        merged.append(contact)
    return sorted(merged, key=lambda c: (c.name.casefold(), c.emails[:1]))


class ContactBook:
    """The merged contacts for the selected email account."""

    def __init__(self, folder, account=''):
        self.folder = folder
        self.account = account or ''
        self.local = AddressBook(folder, self.account)
        self.notice = ''              # why Thunderbird's contacts are missing, when they are
        self.books = []
        self.thunderbird = []         # Thunderbird's contacts as last read, before merging
        self.failed_books = []        # ids of the address books that could not be read
        self.saved_in = ''            # the id of the address book the last new contact went into

    def local_contacts(self):
        return [from_local(entry) for entry in self.local.all().values() if isinstance(entry, dict) and entry.get('name')]

    def load(self, use_thunderbird=True):
        """Read every contact. Thunderbird is asked when usable; a failure leaves the local ones and a notice."""
        self.notice = ''
        self.failed_books = []
        found = []
        if use_thunderbird and thunderbird_pim.usable(self.folder):
            try:
                answer = thunderbird_pim.contacts()
                found = [from_thunderbird(item) for item in answer['contacts'] if isinstance(item, dict)]
                self.books = [book for book in answer.get('books', []) if isinstance(book, dict)]
                self.failed_books = [str(book) for book in answer.get('failedBooks') or [] if book]
                CACHE.update(at=time.monotonic(), contacts=found, books=self.books)
                if answer.get('failed'):
                    self.notice = 'Some Thunderbird address books could not be read. Try again in a minute.'
            except thunderbird_pim.PimError as exc:
                self.notice = str(exc)
                found = []
        elif use_thunderbird:
            self.notice = 'Only contacts saved on this computer are here. To use your Google or Outlook contacts, say add Thunderbird account.'
        self.thunderbird = found
        return merge(found, self.local_contacts())

    def view(self, ids=None):
        """The contacts last read, merged, from every address book (None) or only these book ids.
        LOCAL_ID stands for the contacts saved on this computer. Books are chosen before merging,
        so a person in two accounts is still in each one's list."""
        if ids is None:
            return merge(self.thunderbird, self.local_contacts())
        return merge([c for c in self.thunderbird if c.book_id in ids], self.local_contacts() if LOCAL_ID in ids else [])

    def cached(self):
        """Contacts without asking Thunderbird, unless it is connected and the kept list is old."""
        fresh = CACHE['at'] is not None and time.monotonic() - CACHE['at'] < KEEP_SECONDS
        if not fresh and thunderbird_pim.connected():
            return self.load()
        return merge(CACHE['contacts'] if CACHE['at'] is not None else [], self.local_contacts())

    def preferred_book(self):
        """The Thunderbird address book of the selected account, when Thunderbird links one by its address."""
        for book in self.books or CACHE.get('books', []):
            if self.account and self.account.casefold() in str(book.get('name', '')).casefold() and not book.get('readOnly'):
                return str(book.get('id', ''))
        return ''

    def add(self, name, email='', phone='', book_id='', here=False):
        """Save a new contact: into Thunderbird when it is connected, else on this computer.
        book_id chooses the Thunderbird address book; here saves it on this computer.
        Returns where it was saved, as words."""
        name = name.strip()
        if email:
            from email_draft import normalize_recipient
            email = normalize_recipient(email)
        if not name or len(name) > 80 or re.search(r'[\r\n<>]', name):
            raise ValueError('Use a short contact name without symbols.')
        if not email and not phone.strip():
            raise ValueError('A contact needs an email address or a phone number.')
        self.saved_in = LOCAL_ID
        if not here and thunderbird_pim.usable(self.folder):
            try:
                answer = thunderbird_pim.add_contact(name, email, phone.strip(), book_id or self.preferred_book())
                CACHE['at'] = None
                self.saved_in = str((answer or {}).get('bookId') or '') if isinstance(answer, dict) else ''
                book = str((answer or {}).get('book') or '')
                return 'in Thunderbird' + (', ' + book if book else '')
            except thunderbird_pim.PimError as exc:
                if thunderbird_pim.connected():
                    raise ValueError(str(exc)) from exc
                # Thunderbird could not start: keep the contact here rather than lose it.
        existing = self.local.get(name)
        if existing:
            # Saving would replace the contact already saved under this name, and lose its details.
            raise ValueError('There is already a contact named ' + str(existing.get('name') or name) + '. '
                             'To change it, choose ' + str(existing.get('name') or name) + ', then press F2.')
        self.local.save(name, email, phone.strip())
        return 'on this computer'

    def update(self, contact, name=None, email=None, phone=None):
        """Change a contact's name, first email address or first phone number. None leaves one as it
        is; '' removes it. Thunderbird keeps the rest of the contact. Returns the name afterwards."""
        new_name = contact.name if name is None else name.strip()
        if not new_name or len(new_name) > 80 or re.search(r'[\r\n<>]', new_name):
            raise ValueError('Use a short contact name without symbols.')
        if email:
            from email_draft import normalize_recipient
            email = normalize_recipient(email)
        if phone is not None:
            phone = phone.strip()
            if len(phone) > 40 or re.search(r'[\r\n]', phone):
                raise ValueError('Use a shorter phone number.')
        emails = contact.emails if email is None else ([email] if email else []) + contact.emails[1:]
        phones = contact.phones if phone is None else ([phone] if phone else []) + contact.phones[1:]
        if not emails and not phones:
            raise ValueError('A contact needs an email address or a phone number.')
        if contact.source == 'thunderbird':
            if contact.read_only:
                raise ValueError('This address book cannot be changed, so ' + contact.name + ' was not changed.')
            try:
                thunderbird_pim.update_contact(contact.id, name=None if name is None else new_name, email=email, phone=phone)
            except thunderbird_pim.PimError as exc:
                raise ValueError(str(exc)) from exc
            CACHE['at'] = None
            return new_name
        self.local.replace(contact.name, new_name, emails[0] if emails else '', phones[0] if phones else '')
        return new_name

    def delete(self, contact):
        if contact.source == 'thunderbird':
            if contact.read_only:
                raise ValueError('This address book cannot be changed, so ' + contact.name + ' was not deleted.')
            try:
                thunderbird_pim.delete_contact(contact.id)
            except thunderbird_pim.PimError as exc:
                raise ValueError(str(exc)) from exc
            CACHE['at'] = None
            return
        if not self.local.delete(contact.name):
            raise ValueError(contact.name + ' is no longer in your contacts.')


def find(contacts, spoken, loose=False):
    """Contacts that a spoken name means: the full name; else the first name; else the last name.
    With loose (finding on the Contacts screen), also names that start with the words.
    'John', 'John Smith.', 'mary'."""
    wanted = words(spoken)
    if not wanted:
        return []
    exact = [c for c in contacts if words(c.name) == wanted]
    if exact:
        return exact
    first = [c for c in contacts if words(c.name).split()[:1] == [wanted]]
    if first:
        return first
    last = [c for c in contacts if len(words(c.name).split()) > 1 and words(c.name).split()[-1] == wanted]
    if last:
        return last
    if not loose:
        return []
    return [c for c in contacts if words(c.name).startswith(wanted)][:20]


def address_for(folder, account, spoken):
    """The email address for a contact name said in an email draft, or None.
    Uses the local book first, then Thunderbird's contacts already read (never starts Thunderbird)."""
    book = ContactBook(folder, account)
    entry = book.local.get(str(spoken).strip().rstrip('.!?,;').strip())
    if entry and entry.get('address'):
        return entry['address']
    try:
        matches = [c for c in find(book.cached(), spoken) if c.emails]
    except Exception:
        return None
    if len(matches) == 1 or len({c.emails[0].casefold() for c in matches}) == 1 and matches:
        return matches[0].emails[0]
    return None

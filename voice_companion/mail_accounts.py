"""Saved email accounts: Thunderbird and Yahoo only.

Gmail, Outlook and other mail now work through Thunderbird, which holds the
sign-in. Yahoo uses an app password entered by a helper, never the normal
account password. Account data is encrypted for the signed-in Windows user
using DPAPI.

Older versions also connected Gmail and Microsoft accounts directly. Those are
removed when the app starts (retire_removed_accounts) and are never listed.
"""
import json
import re
import hashlib
from collections import namedtuple
from pathlib import Path


class AccountError(Exception):
    pass


def account_token(provider, folder):
    store = ProtectedStore(folder)
    if provider == 'yahoo':
        saved = store.load('yahoo')
        return saved['address'], saved['app_password']
    if provider == 'thunderbird':
        # Thunderbird holds the sign-in. The "token" is which Thunderbird account to use.
        saved = store.load('thunderbird')
        return saved['address'], saved
    raise AccountError('That email account is not connected. Email works through Thunderbird or Yahoo. Say add account.')


def thunderbird_connect(store):
    """Add every Thunderbird sending address; select the first one."""
    from mailbox_access import MailboxError
    from thunderbird_mailbox import accounts
    try:
        found = accounts()
    except MailboxError as exc:
        raise AccountError(str(exc)) from exc
    identities = [(account, identity) for account in found for identity in account.get('identities', [])
                  if identity.get('email')]
    if not identities:
        raise AccountError('Thunderbird is connected, but it has no email accounts yet. Say add Thunderbird account to add one.')
    for account, identity in identities:
        store.save('thunderbird', {'address': identity['email'], 'account_id': account['id'],
                                   'identity_id': identity['id'], 'name': account.get('name', '')}, select=False)
    first = identities[0][1]['email']
    store.select('thunderbird', first)
    return first


def connect(provider, folder, graphical=False):
    store = ProtectedStore(folder)
    if provider == 'thunderbird':
        return thunderbird_connect(store)
    if provider == 'yahoo':
        if graphical:
            # The app asks for the address and app password in its own speaking
            # text box (companion.ACCOUNT_ENTRY), never in a separate window.
            raise AccountError('Say add account and choose Yahoo.')
        import getpass
        address = input('Yahoo email address: ').strip()
        password = getpass.getpass('Yahoo app password (not your account password): ')
        return yahoo_connect(store, address, password)
    raise AccountError('Email works through Thunderbird or Yahoo. Say add account and choose one.')


YAHOO_SMTP = ('smtp.mail.yahoo.com', 465)
YAHOO_IMAP = ('imap.mail.yahoo.com', 993)
YAHOO_TIMEOUT = 20


def clean_app_password(password):
    """Yahoo shows app passwords in groups, e.g. 'abcd efgh ijkl mnop'. Pasted copies
    may carry spaces, dashes or line breaks; the password itself is the letters."""
    return re.sub(r'[\s-]+', '', password or '')


def yahoo_connect(store, address, password, smtp_factory=None, imap_factory=None):
    """Check the app password with Yahoo's sending and reading servers, then save it.

    Never logs or speaks the password. Every failure is a plain AccountError.
    """
    import smtplib, ssl, imaplib, socket
    from email_draft import normalize_recipient
    try:
        address = normalize_recipient(address)
    except ValueError as exc:
        raise AccountError('That is not a complete email address. Say or type it again, such as sam at yahoo dot com.') from exc
    password = clean_app_password(password)
    if not password:
        raise AccountError('An app password is needed. Type or paste it, then press Enter.')
    context = ssl.create_default_context()
    smtp_factory = smtp_factory or (lambda: smtplib.SMTP_SSL(*YAHOO_SMTP, context=context, timeout=YAHOO_TIMEOUT))
    imap_factory = imap_factory or (lambda: imaplib.IMAP4_SSL(*YAHOO_IMAP, ssl_context=context, timeout=YAHOO_TIMEOUT))
    refused = ('Yahoo did not accept that app password. Check that you copied all of it. '
               'If it still fails, make a new app password in Yahoo Account security, then try again.')
    unreachable = 'Yahoo could not be reached. Check the internet connection, then try again.'
    try:
        with smtp_factory() as smtp:
            smtp.login(address, password)
    except smtplib.SMTPAuthenticationError as exc:
        raise AccountError(refused) from exc
    except (smtplib.SMTPException, OSError, socket.timeout) as exc:
        raise AccountError(unreachable) from exc
    try:
        imap = imap_factory()
    except (OSError, socket.timeout, imaplib.IMAP4.error) as exc:
        raise AccountError(unreachable) from exc
    try:
        imap.login(address, password)
    except imaplib.IMAP4.error as exc:
        raise AccountError(refused) from exc
    except (OSError, socket.timeout) as exc:
        raise AccountError(unreachable) from exc
    finally:
        try: imap.logout()
        except Exception: pass
    store.save('yahoo', {'address': address, 'app_password': password})
    return address


PROVIDERS = ('yahoo', 'thunderbird')
# Kinds older versions connected directly. Their saved files are removed at startup.
REMOVED_PROVIDERS = ('gmail', 'outlook')


class ProtectedStore:
    def __init__(self, folder):
        self.folder = Path(folder)

    def _path(self, provider):
        if provider not in PROVIDERS:
            raise AccountError('That account type is not supported.')
        return self.folder / (provider + '.account')

    def _account_path(self, provider, address):
        digest = hashlib.sha256((provider + ':' + address.casefold()).encode('utf-8')).hexdigest()[:24]
        return self.folder / (provider + '-' + digest + '.account')

    @staticmethod
    def _seal(data):
        import win32crypt
        return win32crypt.CryptProtectData(json.dumps(data).encode('utf-8'),
                                            'Voice Companion mail account', None, None, None, 0)

    @staticmethod
    def _unseal(path):
        import win32crypt
        try:
            return json.loads(win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1])
        except (ValueError, OSError, KeyError) as exc:
            raise AccountError('The saved account cannot be opened. Reconnect it.') from exc

    def accounts(self):
        result = []
        for provider in PROVIDERS:
            paths = list(self.folder.glob(provider + '-*.account')) + [self._path(provider)]
            for path in paths:
                if not path.is_file(): continue
                try:
                    address = self._unseal(path)['address']
                    if (provider, address) not in result: result.append((provider, address))
                except (AccountError, KeyError): continue
        return result

    def selected(self):
        path = self.folder / 'mail-selection.account'
        if path.is_file():
            try:
                choice = self._unseal(path)
            except AccountError:
                choice = None    # A damaged choice is no choice; it must not stop the app.
            if isinstance(choice, dict) and (choice.get('provider'), choice.get('address')) in self.accounts():
                return choice['provider'], choice['address']
        accounts = self.accounts()
        return accounts[0] if len(accounts) == 1 else None

    def select(self, provider, address):
        matches = [(p, a) for p, a in self.accounts() if p == provider and a.casefold() == address.casefold()]
        if len(matches) != 1: raise AccountError('That account is not connected.')
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / 'mail-selection.account'
        temporary = path.with_suffix('.writing')
        temporary.write_bytes(self._seal({'provider': matches[0][0], 'address': matches[0][1]}))
        temporary.replace(path)
        return matches[0]

    def save(self, provider, data, select=True):
        """Store an account. Only connecting an account selects it; a token refresh
        or contacts sync for another account must not switch the sending account."""
        if provider not in PROVIDERS:
            raise AccountError('Email works through Thunderbird or Yahoo. Say add account to connect one.')
        self.folder.mkdir(parents=True, exist_ok=True)
        address = data.get('address')
        if not address: raise AccountError('The account address is missing.')
        encrypted = self._seal(data)
        path = self._account_path(provider, address)
        temporary = path.with_suffix('.writing')
        temporary.write_bytes(encrypted)
        temporary.replace(path)
        if select:
            self.select(provider, address)

    def load(self, provider):
        accounts = [(p,a) for p,a in self.accounts() if p == provider]
        selected = self.selected()
        if selected and selected[0] == provider: address = selected[1]
        elif len(accounts) == 1: address = accounts[0][1]
        elif not accounts: raise AccountError('This email account is not connected.')
        else: raise AccountError('Several accounts are connected. Say switch to followed by the email address.')
        path = self._account_path(provider, address)
        if not path.exists(): path = self._path(provider)
        return self._unseal(path)

    def remove(self, provider, address):
        """Delete one account's saved sign-in, found by type and address.

        Other accounts, even of the same type, are not touched. If this was the
        selected account, the next account in the list (or the one before it)
        is selected. Returns the selected account afterwards, or None."""
        accounts = self.accounts()
        matches = [(p, a) for p, a in accounts if p == provider.casefold() and a.casefold() == address.strip().casefold()]
        if len(matches) != 1:
            raise AccountError('That account is not connected.')
        provider, address = matches[0]
        selected = self.selected()
        position = accounts.index(matches[0])
        paths = [self._account_path(provider, address)]
        legacy = self._path(provider)
        if legacy.is_file():
            try:
                legacy_address = self._unseal(legacy).get('address', '')
            except (AccountError, AttributeError):
                legacy_address = ''
            if legacy_address.casefold() == address.casefold():
                paths.append(legacy)
        for path in paths:
            path.unlink(missing_ok=True)
        if selected != (provider, address):
            return self.selected()
        (self.folder / 'mail-selection.account').unlink(missing_ok=True)
        remaining = [account for account in accounts if account != (provider, address)]
        if not remaining:
            return None
        # The account that followed it in the list, or the one before it.
        choice = remaining[min(position, len(remaining) - 1)]
        return self.select(*choice)

    def disconnect(self, provider):
        choice = self.selected()
        if choice and choice[0] == provider:
            self._account_path(*choice).unlink(missing_ok=True)
            self._path(provider).unlink(missing_ok=True)
            (self.folder / 'mail-selection.account').unlink(missing_ok=True)
        elif len([a for p,a in self.accounts() if p == provider]) == 1:
            address = next(a for p,a in self.accounts() if p == provider)
            self._account_path(provider, address).unlink(missing_ok=True)
            self._path(provider).unlink(missing_ok=True)
        else:
            raise AccountError('Choose the account by its address before disconnecting.')


# What retire_removed_accounts did: the removed (kind, address) pairs (address
# '' when its file could not be read), the account selected afterwards, whether
# that selection changed, and the accounts that remain.
Retired = namedtuple('Retired', 'removed selected changed_selection remaining')


def _removed_files(folder):
    found = []
    for provider in REMOVED_PROVIDERS:
        for pattern in (provider + '-*.account', provider + '.account', provider + '-*.writing', provider + '.writing'):
            found.extend((provider, path) for path in sorted(folder.glob(pattern)) if path.is_file())
    return found


def retire_removed_accounts(folder):
    """Delete saved Gmail and Microsoft accounts that Voice Companion connected directly.

    Only those account files are deleted, never Thunderbird or Yahoo accounts,
    drafts, contacts or notes. A selection naming a removed account is cleared,
    and a remaining Thunderbird account, or else a Yahoo account, is selected.
    A file that cannot be deleted now is tried again next time; it is never
    listed meanwhile. Nothing here raises for a damaged or missing file."""
    folder = Path(folder)
    store = ProtectedStore(folder)
    removed = []
    try:
        files = _removed_files(folder) if folder.is_dir() else []
    except OSError:
        files = []
    if not files:
        return Retired([], None, False, [])
    selection = folder / 'mail-selection.account'
    try:
        choice = store._unseal(selection) if selection.is_file() else None
    except (AccountError, OSError):
        choice = None
    chosen = ((choice.get('provider'), choice.get('address'))
              if isinstance(choice, dict) else None)
    for provider, path in files:
        address = ''
        if path.suffix == '.account':
            try:
                data = store._unseal(path)
                address = data.get('address', '') if isinstance(data, dict) else ''
                address = address if isinstance(address, str) else ''
            except (AccountError, OSError):
                address = ''
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        if path.suffix == '.account' and (provider, address) not in removed:
            removed.append((provider, address))
    if not removed:
        return Retired([], None, False, [])
    remaining = store.accounts()
    changed = False
    if chosen is None or chosen[0] in REMOVED_PROVIDERS or chosen not in remaining:
        # Clear a removed or unreadable choice, then use what is left.
        try:
            selection.unlink(missing_ok=True)
        except OSError:
            pass
        pick = (next((a for a in remaining if a[0] == 'thunderbird'), None) or
                next((a for a in remaining if a[0] == 'yahoo'), None))
        if pick is not None:
            try:
                store.select(*pick)
                changed = True
            except (AccountError, OSError):
                pass
    try:
        now = store.selected()
    except (AccountError, OSError):
        now = None
    return Retired(removed, now, changed, remaining)

"""Windows-only account authorization for the optional email send workflow.

Public OAuth client IDs must be registered by the distributor. Account data is
encrypted for the signed-in Windows user using DPAPI. Yahoo uses an app password
entered by a helper, never the normal account password.
"""
from network_security import secure_service_open
import json
import re
import os
import hashlib
import sys
from pathlib import Path


GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.send',
                'https://www.googleapis.com/auth/gmail.modify',
                'https://www.googleapis.com/auth/userinfo.email', 'openid']
MICROSOFT_SCOPES = ['Mail.Send', 'Mail.ReadWrite', 'User.Read']


class AccountError(Exception):
    pass


def registration(folder):
    """Public client IDs are configuration, never a user's password."""
    path = Path(folder) / 'mail-registration.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    if not isinstance(data, dict):
        raise AccountError('Mail registration is invalid.')
    app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    packaged_registration = app_dir / 'microsoft-registration.json'
    if not data.get('microsoft_client_id') and packaged_registration.is_file():
        supplied = json.loads(packaged_registration.read_text(encoding='utf-8'))
        data['microsoft_client_id'] = supplied['microsoft_client_id']
    if data.get('google_credentials_file'):
        client_path = Path(data['google_credentials_file'])
        if not client_path.is_absolute():
            data['google_credentials_file'] = str(Path(folder) / client_path)
    else:
        # A distributor can ship its public desktop OAuth registration with
        # Setup. A pre-existing Microsoft configuration must not hide it.
        app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        packaged_google = app_dir / 'google-client.json'
        if packaged_google.is_file():
            data['google_credentials_file'] = str(packaged_google)
    return data


def account_token(provider, folder):
    config = registration(folder)
    store = ProtectedStore(folder)
    if provider == 'gmail':
        return gmail_token(store, config.get('google_credentials_file', ''))
    if provider == 'outlook':
        return microsoft_token(store, config.get('microsoft_client_id', ''))
    if provider == 'yahoo':
        saved = store.load('yahoo')
        return saved['address'], saved['app_password']
    if provider == 'thunderbird':
        # Thunderbird holds the sign-in. The "token" is which Thunderbird account to use.
        saved = store.load('thunderbird')
        return saved['address'], saved
    raise AccountError('That provider is not connected.')


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
        raise AccountError('Thunderbird has no email accounts yet. Ask your helper to add your email account in Thunderbird first.')
    for account, identity in identities:
        store.save('thunderbird', {'address': identity['email'], 'account_id': account['id'],
                                   'identity_id': identity['id'], 'name': account.get('name', '')}, select=False)
    first = identities[0][1]['email']
    store.select('thunderbird', first)
    return first


def connect(provider, folder, graphical=False):
    config = registration(folder)
    store = ProtectedStore(folder)
    if provider == 'gmail':
        return gmail_connect(store, config.get('google_credentials_file', ''))
    if provider == 'outlook':
        return microsoft_connect(store, config.get('microsoft_client_id', ''))
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
    raise AccountError('This provider is not supported for account connection yet.')


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


PROVIDERS = ('gmail', 'outlook', 'yahoo', 'thunderbird')


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
            choice = self._unseal(path)
            if (choice.get('provider'), choice.get('address')) in self.accounts():
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


GMAIL_SIGN_IN_SECONDS = 300


def _gmail_sign_in_problem(exc):
    """A plain sentence for a Gmail sign-in that did not finish."""
    again = ' Say add account and choose Gmail to try again.'
    if exc is None or isinstance(exc, AttributeError):
        # No answer from the browser in time (the library has no address to read).
        return 'Gmail sign-in took too long, so it was stopped.' + again
    if getattr(exc, 'error', '') == 'access_denied' or 'access_denied' in str(exc):
        return 'You did not allow Voice Companion to use this Gmail account.' + again
    if isinstance(exc, OSError):
        return 'Gmail sign-in could not open or reach the sign-in page. Check the internet connection.' + again
    return 'Gmail sign-in did not finish.' + again


def gmail_connect(store, client_file):
    path = Path(client_file)
    if not path.is_file():
        raise AccountError('This copy of Voice Companion has no Google app registration. Gmail cannot connect until the distributor provides an updated installer.')
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = json.loads(path.read_text(encoding='utf-8'))
    if 'installed' not in config:
        raise AccountError('A Google desktop app registration is required.')
    # Google may grant fewer permissions when a box is left unticked. Without this,
    # oauthlib stops with a technical "Scope has changed" error; the check below
    # explains it instead.
    os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')
    try:
        credentials = InstalledAppFlow.from_client_config(config, GMAIL_SCOPES).run_local_server(
            port=0, timeout_seconds=GMAIL_SIGN_IN_SECONDS, authorization_prompt_message='',
            success_message='Gmail sign-in is complete. Close this window and go back to Voice Companion.')
    except Exception as exc:
        raise AccountError(_gmail_sign_in_problem(exc)) from exc
    if credentials is None:
        raise AccountError(_gmail_sign_in_problem(None))
    granted = getattr(credentials, 'granted_scopes', None)
    if isinstance(granted, str): granted = granted.split()
    if granted is not None and not {'https://www.googleapis.com/auth/gmail.send',
                                    'https://www.googleapis.com/auth/gmail.modify'}.issubset(set(granted)):
        raise AccountError('Google did not give Voice Companion permission to read and send your mail. '
                           'Say add account, choose Gmail, and sign in again. On the Google page, check every box, then choose Continue.')
    from urllib import request
    # Identify the signed-in account rather than trusting a spoken label.
    if not credentials.valid and credentials.refresh_token:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
    if not credentials.valid:
        raise AccountError('Gmail sign-in did not finish.')
    with secure_service_open(request.Request('https://www.googleapis.com/oauth2/v3/userinfo',
                                       headers={'Authorization': 'Bearer ' + credentials.token}), timeout=30) as response:
        address = json.load(response).get('email')
    if not address:
        raise AccountError('Google did not identify the signed-in email address.')
    store.save('gmail', {'address': address, 'credentials': credentials.to_json(),
                         'client_id': config['installed']['client_id'], 'scopes': GMAIL_SCOPES})
    return address


def gmail_token(store, client_file):
    saved = store.load('gmail')
    if not set(GMAIL_SCOPES).issubset(set(saved.get('scopes', []))):
        raise AccountError('Gmail needs new mail permissions. Ask your helper to reconnect it.')
    if not Path(client_file).is_file():
        raise AccountError('Gmail registration is missing. Ask your helper.')
    config = json.loads(Path(client_file).read_text(encoding='utf-8'))
    if saved['client_id'] != config['installed']['client_id']:
        raise AccountError('The Gmail app registration changed. Reconnect the account.')
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError, TransportError
    credentials = Credentials.from_authorized_user_info(json.loads(saved['credentials']), GMAIL_SCOPES)
    if not credentials.valid:
        if not credentials.refresh_token:
            raise AccountError('Gmail sign-in expired. Reconnect the account.')
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            if getattr(exc, 'retryable', False):
                raise AccountError('Gmail sign-in is temporarily unavailable. Wait a moment and say check email again.') from exc
            raise AccountError('Gmail sign-in expired or was refused. Say add account, choose Gmail, and sign in again to reconnect it.') from exc
        except TransportError as exc:
            raise AccountError('I could not reach Google to renew your Gmail sign-in. Check the internet connection, then say check email again.') from exc
        store.save('gmail', {**saved, 'credentials': credentials.to_json()}, select=False)
    return saved['address'], credentials.token


def microsoft_connect(store, client_id):
    if not client_id:
        raise AccountError('Microsoft sign-in is not set up in this build. Ask your helper.')
    import msal
    from urllib import request
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(client_id, authority='https://login.microsoftonline.com/common',
                                       token_cache=cache)
    result = app.acquire_token_interactive(scopes=MICROSOFT_SCOPES)
    if 'access_token' not in result:
        raise AccountError('Microsoft sign-in did not finish.')
    accounts = app.get_accounts()
    if len(accounts) != 1 or not accounts[0].get('username'):
        raise AccountError('The signed-in Microsoft account could not be identified.')
    with secure_service_open(request.Request('https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName',
                                       headers={'Authorization': 'Bearer ' + result['access_token']}), timeout=30) as response:
        identity = json.load(response)
    address = identity.get('mail') or identity.get('userPrincipalName')
    if not address:
        raise AccountError('Microsoft did not identify the sending mailbox.')
    store.save('outlook', {'address': address, 'client_id': client_id,
                           'home_account_id': accounts[0]['home_account_id'],
                           'cache': cache.serialize()})
    return address


def microsoft_token(store, client_id):
    saved = store.load('outlook')
    if saved['client_id'] != client_id:
        raise AccountError('The Microsoft app registration changed. Reconnect the account.')
    import msal
    cache = msal.SerializableTokenCache()
    cache.deserialize(saved['cache'])
    app = msal.PublicClientApplication(client_id, authority='https://login.microsoftonline.com/common',
                                       token_cache=cache)
    accounts = [a for a in app.get_accounts() if a['home_account_id'] == saved['home_account_id']]
    if len(accounts) != 1:
        raise AccountError('Microsoft sign-in expired. Reconnect the account.')
    result = app.acquire_token_silent(MICROSOFT_SCOPES, account=accounts[0])
    if not result or 'access_token' not in result:
        raise AccountError('Microsoft sign-in expired. Reconnect the account.')
    if cache.has_state_changed:
        store.save('outlook', {**saved, 'cache': cache.serialize()}, select=False)
    return saved['address'], result['access_token']

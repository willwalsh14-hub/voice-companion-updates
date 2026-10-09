"""Windows-only account authorization for the optional email send workflow.

Public OAuth client IDs must be registered by the distributor. Account data is
encrypted for the signed-in Windows user using DPAPI. Yahoo uses an app password
entered by a helper, never the normal account password.
"""
import json
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
    raise AccountError('That provider is not connected.')


def connect(provider, folder, graphical=False):
    config = registration(folder)
    store = ProtectedStore(folder)
    if provider == 'gmail':
        return gmail_connect(store, config.get('google_credentials_file', ''))
    if provider == 'outlook':
        return microsoft_connect(store, config.get('microsoft_client_id', ''))
    if provider == 'yahoo':
        import smtplib
        import ssl
        from email_draft import normalize_recipient
        if graphical:
            address, password = yahoo_account_dialog()
        else:
            import getpass
            address = input('Yahoo email address: ').strip()
            password = getpass.getpass('Yahoo app password (not your account password): ')
        address = normalize_recipient(address)
        if not password.strip():
            raise AccountError('An app password is required.')
        try:
            with smtplib.SMTP_SSL('smtp.mail.yahoo.com', 465,
                                  context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(address, password.strip())
        except (smtplib.SMTPException, OSError) as exc:
            raise AccountError('Yahoo could not verify that app password. No account was saved.') from exc
        store.save('yahoo', {'address': address, 'app_password': password.strip()})
        return address
    raise AccountError('This provider is not supported for account connection yet.')


def yahoo_account_dialog():
    """Private keyboard entry; never read or log the app password."""
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    try:
        address = simpledialog.askstring('Connect Yahoo', 'Yahoo email address:', parent=root)
        if address is None: raise AccountError('Yahoo setup canceled.')
        password = simpledialog.askstring('Connect Yahoo', 'Yahoo app password (not normal password):',
                                          parent=root, show='*')
        if password is None: raise AccountError('Yahoo setup canceled.')
        return address, password
    finally:
        root.destroy()


class ProtectedStore:
    def __init__(self, folder):
        self.folder = Path(folder)

    def _path(self, provider):
        if provider not in ('gmail', 'outlook', 'yahoo'):
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
        for provider in ('gmail', 'outlook', 'yahoo'):
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

    def save(self, provider, data):
        self.folder.mkdir(parents=True, exist_ok=True)
        address = data.get('address')
        if not address: raise AccountError('The account address is missing.')
        encrypted = self._seal(data)
        path = self._account_path(provider, address)
        temporary = path.with_suffix('.writing')
        temporary.write_bytes(encrypted)
        temporary.replace(path)
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


def gmail_connect(store, client_file):
    path = Path(client_file)
    if not path.is_file():
        raise AccountError('This copy of Voice Companion has no Google app registration. Gmail cannot connect until the distributor provides an updated installer.')
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = json.loads(path.read_text(encoding='utf-8'))
    if 'installed' not in config:
        raise AccountError('A Google desktop app registration is required.')
    credentials = InstalledAppFlow.from_client_config(config, GMAIL_SCOPES).run_local_server(port=0)
    from google.auth.transport.requests import Request
    from urllib import request
    # Identify the signed-in account rather than trusting a spoken label.
    if not credentials.valid and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise AccountError('Gmail sign-in did not finish.')
    with request.urlopen(request.Request('https://www.googleapis.com/oauth2/v3/userinfo',
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
        store.save('gmail', {**saved, 'credentials': credentials.to_json()})
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
    with request.urlopen(request.Request('https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName',
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
        store.save('outlook', {**saved, 'cache': cache.serialize()})
    return saved['address'], result['access_token']

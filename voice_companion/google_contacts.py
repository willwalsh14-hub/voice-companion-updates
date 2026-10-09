"""Optional Google Contacts synchronization, separate from Gmail mail consent."""
import json
from pathlib import Path
from urllib import request, parse

from mail_accounts import ProtectedStore, registration, AccountError
from address_book import AddressBook

CONTACT_SCOPES = ['https://www.googleapis.com/auth/contacts',
                  'https://www.googleapis.com/auth/userinfo.email', 'openid']


def _account(folder):
    store = ProtectedStore(folder)
    selected = store.selected()
    if not selected or selected[0] != 'gmail':
        raise AccountError('Select a connected Gmail account before syncing Google Contacts.')
    return store, store.load('gmail')


def _token(folder, authorize=False):
    store, saved = _account(folder)
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    credentials = None
    if saved.get('contacts_credentials'):
        credentials = Credentials.from_authorized_user_info(json.loads(saved['contacts_credentials']), CONTACT_SCOPES)
        if not credentials.valid and credentials.refresh_token:
            credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not authorize:
            raise AccountError('Google Contacts is not connected. Say connect Google Contacts first.')
        from google_auth_oauthlib.flow import InstalledAppFlow
        path = registration(folder).get('google_credentials_file')
        if not path:
            raise AccountError('The Google app registration is missing.')
        config = json.loads(Path(path).read_text(encoding='utf-8'))
        credentials = InstalledAppFlow.from_client_config(config, CONTACT_SCOPES).run_local_server(port=0)
    with request.urlopen(request.Request('https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization':'Bearer '+credentials.token}), timeout=30) as response:
        address = json.load(response).get('email', '')
    if address.casefold() != saved['address'].casefold():
        raise AccountError('Google Contacts signed into a different account. Select the same Gmail address and try again.')
    saved['contacts_credentials'] = credentials.to_json()
    store.save('gmail', saved)
    return credentials.token


def _api(url, token, data=None):
    req = request.Request(url, data=json.dumps(data).encode('utf-8') if data is not None else None,
                          headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
                          method='POST' if data is not None else 'GET')
    with request.urlopen(req, timeout=30) as response:
        return json.load(response)


def sync(folder, authorize=False):
    token = _token(folder, authorize)
    book = AddressBook(folder, ProtectedStore(folder).selected()[1])
    remote = []
    page = ''
    while True:
        url = ('https://people.googleapis.com/v1/people/me/connections?personFields=names,emailAddresses'
               '&pageSize=1000' + ('&pageToken='+parse.quote(page) if page else ''))
        response = _api(url, token)
        for person in response.get('connections', []):
            names = person.get('names', [])
            name = names[0].get('displayName', '') if names else ''
            for item in person.get('emailAddresses', []):
                remote.append((name, item.get('value', '')))
        page = response.get('nextPageToken', '')
        if not page: break
    existing = {(name.casefold(), address.casefold()) for name,address in remote}
    uploaded = 0
    for entry in list(book.all().values()):
        if entry.get('source') == 'google': continue
        name, address = entry['name'], entry['address']
        if (name.casefold(), address.casefold()) not in existing:
            _api('https://people.googleapis.com/v1/people:createContact?personFields=names,emailAddresses',
                 token, {'names':[{'givenName':name}], 'emailAddresses':[{'value':address}]})
            uploaded += 1
            existing.add((name.casefold(), address.casefold()))
    imported = book.import_google(remote)
    return f'Google Contacts synced. {imported} contacts added here; {uploaded} local contacts added to Google.'

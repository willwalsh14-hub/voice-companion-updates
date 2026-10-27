"""Small local address book for spoken names and typed email addresses.

Contacts in Thunderbird's address books are read through contacts_book.py; this
file keeps the contacts saved on this computer only (before Thunderbird was set
up, or while it is not connected)."""
import json
import re
import hashlib
from pathlib import Path

def normalize_recipient(value):
    from email_draft import normalize_recipient as normalize
    return normalize(value)


class AddressBook:
    def __init__(self, folder, account=''):
        suffix = '-' + hashlib.sha256(account.casefold().encode('utf-8')).hexdigest()[:16] if account else ''
        self.path = Path(folder) / ('address-book' + suffix + '.json')

    def all(self):
        try:
            values = json.loads(self.path.read_text(encoding='utf-8'))
            return values if isinstance(values, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, name):
        entry = self.all().get(name.strip().casefold())
        return entry if isinstance(entry, dict) else None

    def save(self, name, address, phone=''):
        """Save a contact. It needs an email address or a phone number; the address is checked."""
        name = name.strip()
        if not name or len(name) > 80 or re.search(r'[\r\n<>]', name):
            raise ValueError('Use a short contact name without symbols.')
        phone = str(phone or '').strip()
        if len(phone) > 40 or re.search(r'[\r\n]', phone):
            raise ValueError('Use a shorter phone number.')
        if address or not phone:
            address = normalize_recipient(address)
        entries = self.all()
        # Contacts once brought in from Google Contacts are ordinary local contacts now.
        entry = {'name': name, 'address': address or ''}
        if phone:
            entry['phone'] = phone
        entries[name.casefold()] = entry
        self._write(entries)
        return address

    def replace(self, old_name, name, address, phone=''):
        """Change a saved contact in one write, so a new name never leaves two copies or none.
        Another contact's name is never taken over."""
        name, phone, address = name.strip(), str(phone or '').strip(), str(address or '').strip()
        if not name or len(name) > 80 or re.search(r'[\r\n<>]', name):
            raise ValueError('Use a short contact name without symbols.')
        if len(phone) > 40 or re.search(r'[\r\n]', phone):
            raise ValueError('Use a shorter phone number.')
        if not address and not phone:
            raise ValueError('A contact needs an email address or a phone number.')
        if address:
            address = normalize_recipient(address)
        entries = self.all()
        old_key, new_key = old_name.strip().casefold(), name.casefold()
        if old_key not in entries:
            raise ValueError(old_name.strip() + ' is no longer in your contacts.')
        if new_key != old_key and new_key in entries:
            other = entries[new_key].get('name', name) if isinstance(entries[new_key], dict) else name
            raise ValueError('There is already a contact named ' + str(other) + '.')
        entry = {'name': name, 'address': address}
        if phone:
            entry['phone'] = phone
        del entries[old_key]
        entries[new_key] = entry
        self._write(entries)
        return address

    def delete(self, name):
        entries = self.all()
        if name.strip().casefold() not in entries: return False
        del entries[name.strip().casefold()]
        self._write(entries)
        return True

    def _write(self, entries):
        # Atomic: a temporary file, then replace, so a crash never leaves half an address book.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.writing.json')
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

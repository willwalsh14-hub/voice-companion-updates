"""Small local address book for spoken names and typed email addresses."""
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

    def save(self, name, address):
        name = name.strip()
        if not name or len(name) > 80 or re.search(r'[\r\n<>]', name):
            raise ValueError('Use a short contact name without symbols.')
        address = normalize_recipient(address)
        entries = self.all()
        # Contacts once brought in from Google Contacts are ordinary local contacts now.
        entries[name.casefold()] = {'name': name, 'address': address}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.writing.json')
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return address

    def delete(self, name):
        entries = self.all()
        if name.strip().casefold() not in entries: return False
        del entries[name.strip().casefold()]
        temporary = self.path.with_suffix('.writing.json')
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return True

"""Yahoo IMAP mailbox access using a helper-created app password."""
import imaplib
import base64
import re
import ssl
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from email.utils import getaddresses

from mailbox_access import MailboxError, html_text, message_date


def encode_folder_name(name):
    result, buffer = [], []
    def flush():
        if buffer:
            encoded = base64.b64encode(''.join(buffer).encode('utf-16-be')).decode().rstrip('=').replace('/', ',')
            result.append('&' + encoded + '-')
            buffer.clear()
    for char in name:
        if 0x20 <= ord(char) <= 0x7e and char != '&':
            flush(); result.append(char)
        elif char == '&':
            flush(); result.append('&-')
        else:
            buffer.append(char)
    flush()
    return ''.join(result)


def decode_folder_name(name):
    def decode(match):
        data = match.group(1)
        if not data: return '&'
        try:
            data = data.replace(',', '/')
            return base64.b64decode(data + '=' * (-len(data) % 4)).decode('utf-16-be')
        except (ValueError, UnicodeError):
            return match.group(0)
    return re.sub(r'&([^-]*)-', decode, name)


class YahooMailbox:
    def __init__(self, address, app_password, connection_factory=imaplib.IMAP4_SSL):
        self.address, self.app_password = address, app_password
        self.connection_factory = connection_factory

    def _connection(self):
        conn = self.connection_factory('imap.mail.yahoo.com', 993,
                                       ssl_context=ssl.create_default_context(), timeout=30)
        try:
            conn.login(self.address, self.app_password)
        except Exception:
            conn.logout()
            raise
        return conn

    def _with(self, function):
        conn = None
        try:
            conn = self._connection()
            return function(conn)
        except MailboxError:
            raise
        except Exception as exc:
            raise MailboxError('Yahoo Mail could not complete that request. Ask your helper to check the connection.') from exc
        finally:
            if conn:
                try: conn.logout()
                except Exception: pass

    @staticmethod
    def _check(status):
        if status != 'OK':
            raise MailboxError('Yahoo Mail did not accept that request.')

    @staticmethod
    def _folder(name, raw=False):
        if not name or any(c in name for c in '\r\n"\\'):
            raise MailboxError('That folder name cannot be used.')
        return '"' + (name if raw else encode_folder_name(name)) + '"'

    def folders(self):
        def operation(conn):
            status, data = conn.list()
            self._check(status)
            result = []
            for entry in data or []:
                if not entry: continue
                # IMAP LIST gives flags, separator, then a quoted or atom name.
                match = re.match(rb'^\([^)]*\)\s+(?:"[^"]*"|NIL)\s+(.+)$', entry)
                if not match: continue
                raw = match.group(1).strip().strip(b'"').decode('ascii', errors='replace')
                result.append((decode_folder_name(raw), raw))
            return result
        return self._with(operation)

    def list_messages(self, folder_id, cursor=None, limit=10):
        if limit not in (10, 20, 30, 40, 50, 100):
            raise MailboxError('Choose 10, 20, 30, 40, 50, or 100 messages.')
        def operation(conn):
            status, _ = conn.select(self._folder(folder_id, raw=True), readonly=True)
            self._check(status)
            status, data = conn.uid('SEARCH', None, 'ALL')
            self._check(status)
            uids = (data[0] or b'').split()
            offset = int(cursor or 0)
            if offset < 0: raise MailboxError('Invalid message page.')
            page = list(reversed(uids))[offset:offset + limit]
            rows = []
            for uid in page:
                status, items = conn.uid('FETCH', uid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] FLAGS INTERNALDATE RFC822.SIZE)')
                self._check(status)
                header = next((v[1] for v in items if isinstance(v, tuple)), b'')
                msg = BytesParser(policy=policy.default).parsebytes(header)
                flags = b' '.join(v[0] if isinstance(v,tuple) else v for v in items if isinstance(v,(tuple,bytes)))
                delivery = re.search(rb'INTERNALDATE\s+"([^"]+)"', flags, re.I)
                rows.append({'id': uid.decode('ascii'),
                             'from': str(make_header(decode_header(msg.get('From', 'Unknown sender')))),
                             'subject': str(make_header(decode_header(msg.get('Subject', '(no subject)')))),
                             'date': message_date(delivery.group(1).decode('ascii') if delivery else msg.get('Date')),
                             'size': int(re.search(rb'RFC822.SIZE\s+(\d+)',flags,re.I)[1]) if re.search(rb'RFC822.SIZE\s+(\d+)',flags,re.I) else 'unavailable',
                             'unread': b'\\Seen' not in flags})
            return rows, str(offset + limit) if len(uids) > offset + limit else None
        return self._with(operation)

    def read_message(self, message_id, folder_id='INBOX'):
        def operation(conn):
            status, _ = conn.select(self._folder(folder_id, raw=True), readonly=True)
            self._check(status)
            status, data = conn.uid('FETCH', self._uid(message_id), '(BODY.PEEK[])')
            self._check(status)
            raw = next((v[1] for v in data if isinstance(v, tuple)), b'')
            if not raw:
                raise MailboxError('That message is no longer available. Say list messages to refresh.')
            if len(raw) > 5 * 1024 * 1024:
                raise MailboxError('This message is too large to read aloud.')
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            part = msg.get_body(preferencelist=('plain', 'html'))
            if part is None: return ''
            content = part.get_content()
            return html_text(content) if part.get_content_type() == 'text/html' else str(content)
        return self._with(operation)

    def response_context(self, message_id, action, own_address, folder_id='INBOX'):
        def operation(conn):
            status, _ = conn.select(self._folder(folder_id, raw=True), readonly=True)
            self._check(status)
            status, data = conn.uid('FETCH', self._uid(message_id), '(BODY.PEEK[])')
            self._check(status)
            raw = next((v[1] for v in data if isinstance(v, tuple)), b'')
            if len(raw) > 5 * 1024 * 1024: raise MailboxError('This message is too large to reply to.')
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            sender = getaddresses([msg.get('Reply-To') or msg.get('From', '')])
            others = getaddresses([msg.get('To', ''), msg.get('Cc', '')]) if action == 'reply_all' else []
            recipients = []
            for _, address in sender + others:
                if address and address.casefold() != own_address.casefold() and address.casefold() not in [a.casefold() for a in recipients]:
                    recipients.append(address)
            subject = msg.get('Subject', '(no subject)')
            prefix = 'Fwd: ' if action == 'forward' else 'Re: '
            if subject.casefold().startswith(prefix.casefold()): prefix = ''
            part = msg.get_body(preferencelist=('plain', 'html'))
            original = '' if part is None else str(part.get_content())
            if part and part.get_content_type() == 'text/html': original = html_text(original)
            return {'action': action, 'source_id': message_id, 'message_id': msg.get('Message-ID', ''),
                    'references': msg.get('References', ''), 'recipient': recipients[0] if action != 'forward' and recipients else '',
                    'cc': recipients[1:] if action == 'reply_all' else [], 'subject': prefix + subject,
                    'original_from': msg.get('From', ''), 'original_subject': subject,
                    'original_body': original if action == 'forward' else ''}
        return self._with(operation)

    @staticmethod
    def _uid(value):
        if not re.fullmatch(r'[1-9]\d*', str(value)):
            raise MailboxError('Invalid message identifier.')
        return str(value)

    def move(self, ids, source_id, destination_id):
        def operation(conn):
            if b'MOVE' not in conn.capabilities:
                raise MailboxError('This Yahoo account does not offer safe message moving.')
            status, _ = conn.select(self._folder(source_id, raw=True))
            self._check(status)
            for ident in ids:
                status, _ = conn.uid('MOVE', self._uid(ident), self._folder(destination_id, raw=True))
                self._check(status)
        return self._with(operation)

    def trash(self, ids, source_id='INBOX'):
        folders = self.folders()
        trash = next((ident for name, ident in folders if name.casefold() == 'trash'), None)
        if not trash:
            raise MailboxError('Yahoo Trash folder could not be identified.')
        return self.move(ids, source_id, trash)

    def folder_info(self, folder_id):
        def operation(conn):
            status, data = conn.status(self._folder(folder_id, raw=True), '(MESSAGES)')
            self._check(status)
            match = re.search(rb'MESSAGES\s+(\d+)', data[0] or b'')
            if not match: raise MailboxError('Yahoo could not count this folder.')
            return {'messagesTotal': int(match.group(1))}
        return self._with(operation)

    def create_folder(self, name, parent_id=None, parent_path=''):
        path = parent_path + name
        def operation(conn):
            status, _ = conn.create(self._folder(path))
            self._check(status)
            return {'name': path}
        return self._with(operation)

    def rename_folder(self, folder_id, new_name, parent_path=''):
        return self.move_folder(folder_id, folder_id, None, parent_path, new_name)

    def move_folder(self, folder_id, old_path, destination_id, destination_path, new_name=None):
        target = destination_path + (new_name or old_path.rsplit('/', 1)[-1])
        def operation(conn):
            status, _ = conn.rename(self._folder(folder_id, raw=True), self._folder(target))
            self._check(status)
            return {'name': target}
        return self._with(operation)

    def delete_folder(self, folder_id):
        def operation(conn):
            status, _ = conn.delete(self._folder(folder_id, raw=True))
            self._check(status)
        return self._with(operation)

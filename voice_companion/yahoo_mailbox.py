"""Yahoo Mail through IMAP, using a helper-created app password.

Yahoo answers on imap.mail.yahoo.com, port 993, over SSL. Messages are named by
UID, never by sequence number, so a command always reaches the message the
user heard. Each request opens its own short connection with a time limit, so a
slow or lost connection cannot freeze the app. When a connection drops before
anything could have changed, the request is tried once more.

Yahoo's own folders are Inbox, Sent, Draft, Trash, Bulk (spam) and Archive.
They are named here the way Yahoo Mail shows them (Drafts, Spam), so the same
spoken commands work as on Gmail and Outlook.
"""
import base64
import imaplib
import re
import ssl
import time
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses

from mailbox_access import MailboxError, html_text, message_date, part_text

HOST, PORT = 'imap.mail.yahoo.com', 993
TIMEOUT = 30
MAX_MESSAGE = 5 * 1024 * 1024
HEADER_ITEMS = '(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])'

SIGN_IN_FAILED = ('Yahoo did not accept the saved app password. It may have been removed or changed. '
                  'Ask your helper to make a new Yahoo app password, then say add account.')
NO_ANSWER = 'Yahoo Mail did not answer. Check the internet connection, then try again.'
UNCERTAIN = 'Yahoo Mail stopped answering partway through. Say list messages and check before trying again.'
BUSY = 'Yahoo Mail could not do that right now. Wait a minute, then try again.'

# Special-use flags (RFC 6154), and the names Yahoo gives its own folders.
ROLE_FLAGS = {'\\sent': 'sent', '\\drafts': 'drafts', '\\trash': 'trash', '\\junk': 'spam', '\\archive': 'archive'}
YAHOO_NAMES = {'inbox': 'inbox', 'sent': 'sent', 'draft': 'drafts', 'drafts': 'drafts', 'trash': 'trash',
               'bulk': 'spam', 'bulk mail': 'spam', 'spam': 'spam', 'archive': 'archive'}
SPOKEN_NAMES = {'inbox': 'Inbox', 'sent': 'Sent', 'drafts': 'Drafts', 'trash': 'Trash', 'spam': 'Spam',
                'archive': 'Archive'}


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


class _Unchanged(Exception):
    """A connection failed before any change was sent, so a retry is safe."""


def _text(value):
    """Plain text for speech: no surrogates left over from 8-bit headers, no folding."""
    value = str(value or '')
    value = value.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
    return ' '.join(value.split())


def _header(msg, name, fallback=''):
    try:
        return _text(msg.get(name)) or fallback
    except (ValueError, TypeError, LookupError, IndexError, AttributeError):
        # A damaged header must not hide the whole message list.
        raw = msg.get_all(name, failobj=[''])
        return _text(raw[0] if raw else '') or fallback


def message_body(msg):
    """Readable body of one message, the same way Gmail messages are read."""
    from email_reader import plain_body
    try:
        part = msg.get_body(preferencelist=('html', 'plain'))
        if part is None:
            return ''
        content = part_text(part)
        body = html_text(content) if part.get_content_type() == 'text/html' else plain_body(content)
        plain = msg.get_body(preferencelist=('plain',))
        if plain is not None:
            body.plain = str(plain_body(part_text(plain)))
        return body
    except (ValueError, TypeError, UnicodeError, LookupError, KeyError, AttributeError) as exc:
        raise MailboxError('This message could not be read. Say next message to go on.') from exc


def _internal_date(value):
    # IMAP writes early days with a space: " 5-Oct-2026 10:00:00 +0000".
    value = value.strip()
    if re.match(r'\d-', value):
        value = '0' + value
    return value


class YahooMailbox:
    def __init__(self, address, app_password, connection_factory=imaplib.IMAP4_SSL):
        # Yahoo shows app passwords in groups of four letters; spaces are not part of them.
        self.address, self.app_password = address, ''.join(str(app_password or '').split())
        self.connection_factory = connection_factory
        self.timeout = TIMEOUT
        self._changing = False

    # Connection -------------------------------------------------------

    def _connection(self):
        try:
            conn = self.connection_factory(HOST, PORT, ssl_context=ssl.create_default_context(),
                                           timeout=self.timeout)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise _Unchanged() from exc
        try:
            conn.login(self.address, self.app_password)
        except imaplib.IMAP4.abort as exc:
            self._drop(conn)
            raise _Unchanged() from exc
        except imaplib.IMAP4.error as exc:
            self._drop(conn)
            reason = str(exc).upper()
            if 'AUTHENTICATIONFAILED' in reason or 'INVALID CREDENTIALS' in reason or 'AUTHORIZATIONFAILED' in reason:
                raise MailboxError(SIGN_IN_FAILED) from exc
            raise MailboxError(BUSY) from exc
        except OSError as exc:
            self._drop(conn)
            raise _Unchanged() from exc
        return conn

    @staticmethod
    def _drop(conn):
        # After a failure, close at once; a polite LOGOUT could wait for a dead server.
        try: conn.shutdown()
        except Exception: pass

    def _with(self, function, retry=True):
        """Run one request. A connection that drops is tried once more, but only
        when nothing was changed yet (or the request only reads). Every error
        becomes a MailboxError that says whether the mailbox could have changed."""
        for attempt in (1, 2):
            conn = None
            self._changing = False
            try:
                conn = self._connection()
                result = function(conn)
            except MailboxError as exc:
                if conn is not None: self._drop(conn)
                exc.unchanged = exc.unchanged or not self._changing
                raise
            except _Unchanged as exc:
                if attempt == 1: continue
                raise MailboxError(NO_ANSWER, unchanged=True) from exc.__cause__
            except (OSError, imaplib.IMAP4.abort) as exc:
                self._drop(conn)
                if attempt == 1 and (retry or not self._changing):
                    continue
                raise MailboxError(UNCERTAIN if self._changing else NO_ANSWER,
                                   unchanged=not self._changing) from exc
            except imaplib.IMAP4.error as exc:
                self._drop(conn)
                raise MailboxError(UNCERTAIN if self._changing else BUSY,
                                   unchanged=not self._changing) from exc
            try: conn.logout()
            except Exception: self._drop(conn)
            return result

    @staticmethod
    def _check(status, message=BUSY):
        if status != 'OK':
            raise MailboxError(message)

    @staticmethod
    def _folder(name, raw=False):
        if not name or any(c in name for c in '\r\n"\\'):
            raise MailboxError('That folder name cannot be used.')
        return '"' + (name if raw else encode_folder_name(name)) + '"'

    @staticmethod
    def _capabilities(conn):
        # imaplib stores capabilities as text ('MOVE'); accept bytes too for older callers.
        found = {(c.decode() if isinstance(c, bytes) else c).upper() for c in conn.capabilities}
        if 'MOVE' not in found:
            # Python 3.12 keeps the list from before sign-in; Yahoo may add MOVE after it.
            try:
                status, data = conn.capability()
                if status == 'OK' and data and data[-1]:
                    words = data[-1].decode() if isinstance(data[-1], bytes) else str(data[-1])
                    found.update(words.upper().split())
            except imaplib.IMAP4.error:
                pass
        return found

    def _select(self, conn, folder_id, readonly=True):
        status, data = conn.select(self._folder(folder_id or 'INBOX', raw=True), readonly=readonly)
        self._check(status, 'That folder is no longer in Yahoo Mail. Say list folders to hear the folders.')
        try:
            return int(data[0] or 0)
        except (TypeError, ValueError, IndexError):
            return 0

    # Folders ----------------------------------------------------------

    def _folder_list(self, conn):
        status, data = conn.list()
        self._check(status)
        entries = []
        for item in data or []:
            # imaplib gives a name sent as a literal as a (prefix, name) pair.
            if isinstance(item, tuple):
                prefix = re.sub(rb'\{\d+\+?\}\s*$', b'', (item[0] or b'').rstrip())
                name = (item[1] or b'').replace(b'\\', b'\\\\').replace(b'"', b'\\"')
                entries.append(prefix.rstrip() + b' "' + name + b'"')
            elif isinstance(item, bytes) and item:
                entries.append(item)
        found, delimiter = [], '/'
        for entry in entries:
            match = re.match(rb'^\(([^)]*)\)\s+("(?:[^"\\]|\\.)*"|NIL)\s+(.+?)\s*$', entry)
            if not match:
                continue
            flags = match.group(1).decode('ascii', 'replace').lower().split()
            if match.group(2) != b'NIL':
                delimiter = re.sub(r'\\(.)', r'\1', match.group(2)[1:-1].decode('ascii', 'replace')) or '/'
            raw = match.group(3)
            if raw.startswith(b'"') and raw.endswith(b'"'):
                raw = re.sub(rb'\\(.)', rb'\1', raw[1:-1])
            raw = raw.decode('utf-8', 'replace')
            if '\\noselect' in flags or '\\nonexistent' in flags:
                continue
            role = next((ROLE_FLAGS[f] for f in flags if f in ROLE_FLAGS), None)
            if raw.casefold() == 'inbox':
                role = 'inbox'
            elif role is None and delimiter not in raw:
                role = YAHOO_NAMES.get(raw.casefold())
            found.append({'raw': raw, 'role': role, 'delimiter': delimiter,
                          'name': decode_folder_name(raw).replace(delimiter, '/') if delimiter != '/' else decode_folder_name(raw)})
        names = [item['name'].casefold() for item in found]
        for item in found:
            spoken = SPOKEN_NAMES.get(item['role'])
            if spoken and spoken.casefold() != item['name'].casefold() and spoken.casefold() not in names:
                item['name'] = spoken
        self.delimiter = delimiter
        return found

    def _special(self, conn, role):
        return next((item for item in self._folder_list(conn) if item['role'] == role), None)

    def folders(self):
        return self._with(lambda conn: [(item['name'], item['raw']) for item in self._folder_list(conn)])

    def folder_info(self, folder_id):
        def operation(conn):
            status, data = conn.status(self._folder(folder_id, raw=True), '(MESSAGES)')
            self._check(status, 'That folder is no longer in Yahoo Mail. Say list folders to hear the folders.')
            text = b' '.join(part for item in data or [] for part in (item if isinstance(item, tuple) else (item,))
                             if isinstance(part, bytes))
            match = re.search(rb'MESSAGES\s+(\d+)', text)
            if not match: raise MailboxError('Yahoo could not count this folder. Try again in a minute.')
            return {'messagesTotal': int(match.group(1))}
        return self._with(operation)

    def _path(self, path):
        delimiter = getattr(self, 'delimiter', '/') or '/'
        return encode_folder_name(path.replace('/', delimiter))

    def create_folder(self, name, parent_id=None, parent_path=''):
        path = parent_path + name
        def operation(conn):
            self._folder_list(conn)
            self._changing = True
            status, _ = conn.create(self._folder(self._path(path), raw=True))
            self._check(status, 'Yahoo did not make that folder. It may already exist. Say list folders to check.')
            return {'name': path}
        return self._with(operation, retry=False)

    def rename_folder(self, folder_id, new_name, parent_path=''):
        return self.move_folder(folder_id, folder_id, None, parent_path, new_name)

    def move_folder(self, folder_id, old_path, destination_id, destination_path, new_name=None):
        target = destination_path + (new_name or old_path.rsplit('/', 1)[-1])
        def operation(conn):
            self._folder_list(conn)
            self._changing = True
            status, _ = conn.rename(self._folder(folder_id, raw=True), self._folder(self._path(target), raw=True))
            self._check(status, 'Yahoo did not change that folder. Say list folders to check it.')
            return {'name': target}
        return self._with(operation, retry=False)

    def delete_folder(self, folder_id):
        def operation(conn):
            self._changing = True
            status, _ = conn.delete(self._folder(folder_id, raw=True))
            self._check(status, 'Yahoo did not delete that folder. Say list folders to check it.')
        return self._with(operation, retry=False)

    # Messages ---------------------------------------------------------

    @staticmethod
    def _uid(value):
        if not re.fullmatch(r'[1-9]\d*', str(value)):
            raise MailboxError('Invalid message identifier.')
        return str(value)

    @staticmethod
    def _cursor(cursor):
        """A page is "how many newer messages to skip:folder size when counted"."""
        if not cursor:
            return 0, None
        offset, _, total = str(cursor).partition(':')
        if not offset.isdigit() or (total and not total.isdigit()):
            raise MailboxError('Invalid message page.')
        return int(offset), (int(total) if total else None)

    @staticmethod
    def _fetch_records(data):
        """Group an imaplib FETCH reply by message: {sequence: (details, literal)}."""
        records, current = {}, None
        for item in data or []:
            if isinstance(item, tuple):
                head = item[0] or b''
                match = re.match(rb'(\d+) \(', head)
                if not match:
                    continue
                current = records.setdefault(int(match.group(1)), [b'', b''])
                current[0] += b' ' + head
                current[1] = item[1] or b''
            elif isinstance(item, bytes):
                match = re.match(rb'(\d+) \(', item)
                if match:
                    current = records.setdefault(int(match.group(1)), [b'', b''])
                    current[0] += b' ' + item
                elif current is not None:
                    current[0] += b' ' + item
        return records

    def list_messages(self, folder_id, cursor=None, limit=10):
        if limit not in (10, 20, 30, 40, 50, 100):
            raise MailboxError('Choose 10, 20, 30, 40, 50, or 100 messages.')
        offset, counted = self._cursor(cursor)
        def operation(conn):
            total = self._select(conn, folder_id)
            # New mail arrives at the end; keep the next page where it was.
            skip = offset + (total - counted if counted is not None and total > counted else 0)
            newest = total - skip
            if newest < 1:
                return [], None
            oldest = max(1, newest - limit + 1)
            status, data = conn.fetch(str(oldest) + ':' + str(newest), HEADER_ITEMS)
            self._check(status)
            rows = []
            for sequence, (details, header) in sorted(self._fetch_records(data).items(), reverse=True):
                uid = re.search(rb'\bUID (\d+)', details)
                if not uid or not oldest <= sequence <= newest:
                    continue
                msg = BytesParser(policy=policy.default).parsebytes(header or b'', headersonly=True)
                flags = re.search(rb'FLAGS \(([^)]*)\)', details)
                delivery = re.search(rb'INTERNALDATE "([^"]+)"', details)
                size = re.search(rb'RFC822\.SIZE (\d+)', details)
                rows.append({'id': uid.group(1).decode('ascii'),
                             'from': _header(msg, 'From', 'Unknown sender'),
                             'subject': _header(msg, 'Subject', '(no subject)'),
                             'date': message_date(_internal_date(delivery.group(1).decode('ascii')) if delivery
                                                  else _header(msg, 'Date')),
                             'size': int(size.group(1)) if size else 'unavailable',
                             'unread': not flags or b'\\seen' not in flags.group(1).lower()})
            following = str(skip + limit) + ':' + str(total) if oldest > 1 else None
            return rows, following
        return self._with(operation)

    def _message(self, conn, message_id, folder_id, too_large):
        self._select(conn, folder_id)
        uid = self._uid(message_id)
        status, data = conn.uid('FETCH', uid, '(RFC822.SIZE)')
        self._check(status)
        records = self._fetch_records(data)
        if not records:
            raise MailboxError('That message is no longer available. Say list messages to refresh.')
        details = b' '.join(record[0] for record in records.values())
        found = re.search(rb'RFC822\.SIZE (\d+)', details)
        if found and int(found.group(1)) > MAX_MESSAGE:
            raise MailboxError(too_large)
        status, data = conn.uid('FETCH', uid, '(BODY.PEEK[])')
        self._check(status)
        raw = next((record[1] for record in self._fetch_records(data).values() if record[1]), b'')
        if not raw:
            raise MailboxError('That message is no longer available. Say list messages to refresh.')
        if len(raw) > MAX_MESSAGE:
            raise MailboxError(too_large)
        return BytesParser(policy=policy.default).parsebytes(raw)

    def read_message(self, message_id, folder_id='INBOX'):
        return self._with(lambda conn: message_body(
            self._message(conn, message_id, folder_id, 'This message is too large to read aloud.')))

    def response_context(self, message_id, action, own_address, folder_id='INBOX'):
        if action not in ('reply', 'reply_all', 'forward'):
            raise MailboxError('Unknown email response.')
        def operation(conn):
            msg = self._message(conn, message_id, folder_id, 'This message is too large to reply to.')
            sender = getaddresses([_header(msg, 'Reply-To') or _header(msg, 'From')])
            others = getaddresses([_header(msg, 'To'), _header(msg, 'Cc')]) if action == 'reply_all' else []
            recipients = []
            for _, address in sender + others:
                if address and address.casefold() != own_address.casefold() and address.casefold() not in [a.casefold() for a in recipients]:
                    recipients.append(address)
            subject = _header(msg, 'Subject', '(no subject)')
            prefix = 'Fwd: ' if action == 'forward' else 'Re: '
            if subject.casefold().startswith(prefix.casefold()): prefix = ''
            return {'action': action, 'source_id': str(message_id), 'message_id': _header(msg, 'Message-ID'),
                    'references': _header(msg, 'References'),
                    'recipient': recipients[0] if action != 'forward' and recipients else '',
                    'cc': recipients[1:] if action == 'reply_all' else [], 'subject': prefix + subject,
                    'original_from': _header(msg, 'From'), 'original_subject': subject,
                    'original_body': message_body(msg) if action == 'forward' else ''}
        return self._with(operation)

    @staticmethod
    def _same(first, second):
        return str(first).casefold() == str(second).casefold()

    def _move(self, conn, uids, destination):
        capabilities = self._capabilities(conn)
        uid_set = ','.join(uids)
        target = self._folder(destination, raw=True)
        if 'MOVE' in capabilities:
            self._changing = True
            status, _ = conn.uid('MOVE', uid_set, target)
            self._check(status, 'Yahoo did not move the messages. Say list messages and check before trying again.')
        elif 'UIDPLUS' in capabilities:
            # Copy, then remove only these messages; never a plain EXPUNGE.
            self._changing = True
            status, _ = conn.uid('COPY', uid_set, target)
            self._check(status, 'Yahoo did not move the messages. Say list messages and check before trying again.')
            status, _ = conn.uid('STORE', uid_set, '+FLAGS.SILENT', '(\\Deleted)')
            self._check(status, UNCERTAIN)
            status, _ = conn.uid('EXPUNGE', uid_set)
            self._check(status, UNCERTAIN)
        else:
            raise MailboxError('Yahoo did not offer a safe way to move messages. Nothing was changed.')

    def move(self, ids, source_id, destination_id):
        uids = [self._uid(ident) for ident in ids]
        def operation(conn):
            if not uids or self._same(source_id, destination_id):
                return
            self._select(conn, source_id, readonly=False)
            self._move(conn, uids, destination_id)
        return self._with(operation, retry=False)

    def trash(self, ids, source_id='INBOX'):
        """Move to Trash, as Gmail does. Yahoo empties Trash later on its own."""
        uids = [self._uid(ident) for ident in ids]
        def operation(conn):
            trash = self._special(conn, 'trash')
            if not trash:
                raise MailboxError('Yahoo Trash folder could not be found. Nothing was deleted. Ask your helper to check the account.')
            if not uids or self._same(source_id, trash['raw']):
                return  # Already in Trash, as on Gmail.
            self._select(conn, source_id, readonly=False)
            self._move(conn, uids, trash['raw'])
        return self._with(operation, retry=False)

    # Sent copy ---------------------------------------------------------

    def save_sent_copy(self, raw, message_id=''):
        """Put a copy of a message sent through Yahoo SMTP in Sent, like Gmail.

        Yahoo does not reliably keep one itself. If Yahoo already has a copy
        with the same Message-ID, nothing is added."""
        def operation(conn):
            sent = self._special(conn, 'sent')
            if not sent:
                raise MailboxError('Yahoo Sent folder could not be found.')
            if message_id and re.fullmatch(r'<[^<>"\\\s]+>', message_id):
                self._select(conn, sent['raw'])
                status, data = conn.uid('SEARCH', None, 'HEADER', 'Message-ID', '"' + message_id + '"')
                if status == 'OK' and data and data[0] and data[0].split():
                    return 'already saved'
            self._changing = True
            status, _ = conn.append(self._folder(sent['raw'], raw=True), '(\\Seen)', time.time(), raw)
            self._check(status)
            return 'saved'
        # A retry first checks for the Message-ID, so it cannot add a second copy.
        return self._with(operation, retry=bool(message_id))

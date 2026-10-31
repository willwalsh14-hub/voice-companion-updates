"""Test-only stand-in for Gmail's IMAP and SMTP servers, for a real Thunderbird to talk to.

Not part of the app. Used by check_thunderbird_marks_live.py on Windows CI. It behaves like
Gmail where Voice Companion relies on it:
  - X-GM-EXT-1, so Thunderbird treats the account as Gmail (X-GM-MSGID, X-GM-THRID, X-GM-LABELS).
  - Labels are folders: one message can be in INBOX and [Gmail]/Important at once. Read and
    flagged belong to the message, so they show in every folder it is in. Copying into a label
    folder adds the label; deleting and expunging there removes only the label.
  - Mail sent through SMTP gets a copy in [Gmail]/Sent Mail, as Gmail files it.
  - PREAUTH greeting: no password, so Thunderbird never asks for one.
It can be told to refuse the next STOREs (answer NO), to show how Voice Companion recovers.
"""
import re
import socket
import threading
import time

SEEN, FLAGGED, DELETED = '\\Seen', '\\Flagged', '\\Deleted'
FOLDERS = ('INBOX', '[Gmail]', '[Gmail]/All Mail', '[Gmail]/Important', '[Gmail]/Sent Mail', '[Gmail]/Starred',
           '[Gmail]/Trash', '[Gmail]/Drafts', '[Gmail]/Spam')
SPECIAL = {'[Gmail]': '\\Noselect \\HasChildren', '[Gmail]/All Mail': '\\HasNoChildren \\All',
           '[Gmail]/Important': '\\HasNoChildren \\Important', '[Gmail]/Sent Mail': '\\HasNoChildren \\Sent',
           '[Gmail]/Starred': '\\HasNoChildren \\Flagged', '[Gmail]/Trash': '\\HasNoChildren \\Trash',
           '[Gmail]/Drafts': '\\HasNoChildren \\Drafts', '[Gmail]/Spam': '\\HasNoChildren \\Junk'}
CAPABILITIES = 'IMAP4rev1 UNSELECT UIDPLUS SPECIAL-USE X-GM-EXT-1 LITERAL+'


class Message:
    def __init__(self, raw, msgid, thrid, flags=()):
        self.raw = raw if isinstance(raw, bytes) else raw.encode('utf-8')
        self.msgid = msgid
        self.thrid = thrid
        self.flags = set(flags)
        self.date = time.strftime('%d-%b-%Y %H:%M:%S +0000', time.gmtime())

    def header_value(self, name):
        head = self.raw.split(b'\r\n\r\n', 1)[0].decode('utf-8', 'replace')
        found = re.search(r'^' + re.escape(name) + r':[ \t]*(.*)$', head, re.I | re.M)
        return found.group(1).strip() if found else ''


class Mailbox:
    def __init__(self, name):
        self.name = name
        self.uids = []        # [(uid, Message)]
        self.next_uid = 1
        self.deleted = set()  # uids marked \Deleted here (only here: \Deleted is per label in Gmail)

    def add(self, message):
        uid = self.next_uid
        self.next_uid += 1
        self.uids.append((uid, message))
        return uid

    def has(self, message):
        return any(m is message for _, m in self.uids)


class FakeGmail:
    def __init__(self, address='trial@gmail.com'):
        self.address = address
        self.boxes = {name: Mailbox(name) for name in FOLDERS}
        self.lock = threading.RLock()
        self.next_id = 1880500000000000000
        self.refuse_stores = 0      # answer NO to this many STORE commands
        self.log = []               # every command, for the report
        self.smtp_messages = []     # every message Thunderbird sent through SMTP
        self.sockets = []

    # ---- the mailbox as Gmail keeps it ----

    def deliver(self, subject, sender='noreply@uber.com', thread=None, extra_headers='', body='Thank you for riding.'):
        with self.lock:
            self.next_id += 1
            msgid = self.next_id
            message_id = '<%d@fake.gmail.example>' % msgid
            raw = ('From: Uber Receipts <%s>\r\nTo: %s\r\nSubject: %s\r\nDate: %s\r\nMessage-ID: %s\r\n%s'
                   'MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n') % (
                sender, self.address, subject, time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime()),
                message_id, extra_headers, body)
            message = Message(raw, msgid, thread or msgid)
            self.boxes['INBOX'].add(message)
            self.boxes['[Gmail]/All Mail'].add(message)
            return message

    def find(self, message_id):
        with self.lock:
            for _, message in self.boxes['[Gmail]/All Mail'].uids:
                if message.header_value('Message-ID').strip('<>') == message_id.strip('<>'):
                    return message
        return None

    def labels(self, message):
        with self.lock:
            return [name for name, box in self.boxes.items() if box.has(message) and name != '[Gmail]/All Mail']

    # ---- IMAP ----

    def serve_imap(self, port):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', port))
        server.listen(20)
        self.sockets.append(server)
        threading.Thread(target=self._accept, args=(server, self._imap_client), daemon=True).start()

    def serve_smtp(self, port):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', port))
        server.listen(20)
        self.sockets.append(server)
        threading.Thread(target=self._accept, args=(server, self._smtp_client), daemon=True).start()

    def close(self):
        for server in self.sockets:
            try:
                server.close()
            except OSError:
                pass

    def _accept(self, server, handler):
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=self._guard, args=(handler, connection), daemon=True).start()

    def _guard(self, handler, connection):
        try:
            handler(connection)
        except Exception as exc:   # a broken connection ends that client only
            self.log.append('connection error: %s: %s' % (type(exc).__name__, exc))
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _imap_client(self, connection):
        reader = connection.makefile('rb')
        send = lambda text: connection.sendall(text if isinstance(text, bytes) else text.encode('utf-8'))
        state = {'box': None, 'readonly': False}
        send('* PREAUTH [CAPABILITY %s] Fake Gmail ready\r\n' % CAPABILITIES)
        while True:
            line = reader.readline()
            if not line:
                return
            line = line.rstrip(b'\r\n')
            # A literal ({n} or {n+}) continues the command on the next lines.
            while re.search(rb'\{(\d+)\+?\}$', line):
                size = int(re.search(rb'\{(\d+)\+?\}$', line).group(1))
                if not line.endswith(b'+}'):
                    send('+ go ahead\r\n')
                data = reader.read(size)
                line = line[:line.rfind(b'{')] + b'"' + data.replace(b'"', b'\\"') + b'"' + reader.readline().rstrip(b'\r\n')
            text = line.decode('utf-8', 'replace')
            self.log.append('IMAP ' + text[:300])
            tag, _, rest = text.partition(' ')
            command, _, args = rest.partition(' ')
            command = command.upper()
            if command == 'UID':
                sub, _, args = args.partition(' ')
                command = 'UID ' + sub.upper()
            try:
                if command == 'LOGOUT':
                    send('* BYE logging out\r\n%s OK LOGOUT completed\r\n' % tag)
                    return
                if command == 'IDLE':
                    send('+ idling\r\n')
                    reader.readline()
                    send('%s OK IDLE terminated\r\n' % tag)
                    continue
                untagged, status = self._imap(command, args, state)
                for item in untagged:
                    send(item)
                send('%s %s\r\n' % (tag, status))
            except Exception as exc:
                self.log.append('IMAP error: %s: %s' % (type(exc).__name__, exc))
                send('%s BAD %s\r\n' % (tag, type(exc).__name__))

    def _imap(self, command, args, state):
        with self.lock:
            if command in ('CAPABILITY',):
                return ['* CAPABILITY %s\r\n' % CAPABILITIES], 'OK CAPABILITY completed'
            if command in ('NOOP', 'CHECK', 'SUBSCRIBE', 'UNSUBSCRIBE', 'LOGIN', 'AUTHENTICATE', 'ENABLE', 'ID'):
                return self._exists(state), 'OK %s completed' % command
            if command in ('LIST', 'LSUB', 'XLIST'):
                return self._list(command, args), 'OK %s completed' % command
            if command in ('SELECT', 'EXAMINE'):
                name = unquote(args.split(' (')[0])
                if name.upper() == 'INBOX':
                    name = 'INBOX'
                box = self.boxes.get(name)
                if not box or name == '[Gmail]':
                    return [], 'NO [NONEXISTENT] No such folder'
                state['box'] = box
                state['readonly'] = command == 'EXAMINE'
                state['seen_count'] = len(box.uids)
                state['known'] = {uid: self._flags(box, uid, m) for uid, m in box.uids}
                return ['* FLAGS (\\Answered \\Flagged \\Draft \\Deleted \\Seen $Forwarded $MDNSent Junk NonJunk)\r\n',
                        '* OK [PERMANENTFLAGS (\\Answered \\Flagged \\Draft \\Deleted \\Seen $Forwarded $MDNSent Junk NonJunk \\*)] Flags permitted.\r\n',
                        '* OK [UIDVALIDITY 7] UIDs valid.\r\n',
                        '* %d EXISTS\r\n' % len(box.uids), '* 0 RECENT\r\n',
                        '* OK [UIDNEXT %d] Predicted next UID.\r\n' % box.next_uid,
                        '* OK [HIGHESTMODSEQ 1]\r\n'], 'OK [%s] %s completed' % (
                            'READ-ONLY' if state['readonly'] else 'READ-WRITE', command)
            if command == 'STATUS':
                name = unquote(args.rsplit(' (', 1)[0])
                box = self.boxes.get('INBOX' if name.upper() == 'INBOX' else name)
                if not box:
                    return [], 'NO no such folder'
                unseen = sum(1 for _, m in box.uids if SEEN not in m.flags)
                return ['* STATUS %s (MESSAGES %d UIDNEXT %d UNSEEN %d RECENT 0)\r\n' % (
                    quote(box.name), len(box.uids), box.next_uid, unseen)], 'OK STATUS completed'
            if command in ('CLOSE', 'UNSELECT'):
                if command == 'CLOSE' and state['box'] and not state['readonly']:
                    self._expunge(state['box'])
                state['box'] = None
                return [], 'OK %s completed' % command
            box = state['box']
            if box is None:
                return [], 'BAD No folder selected'
            if command in ('FETCH', 'UID FETCH'):
                return self._exists(state) + self._fetch(box, args, command == 'UID FETCH'), 'OK FETCH completed'
            if command in ('STORE', 'UID STORE'):
                if self.refuse_stores > 0:
                    self.refuse_stores -= 1
                    return [], 'NO [UNAVAILABLE] Temporary failure, try again'
                lines = self._store(box, args, command == 'UID STORE')
                state.setdefault('known', {}).update({uid: self._flags(box, uid, m) for uid, m in box.uids})
                return lines, 'OK STORE completed'
            if command in ('COPY', 'UID COPY'):
                return self._copy(box, args, command == 'UID COPY')
            if command in ('EXPUNGE', 'UID EXPUNGE'):
                return self._expunge(box), 'OK EXPUNGE completed'
            if command in ('SEARCH', 'UID SEARCH'):
                uids = [str(uid) for uid, _ in box.uids]
                return ['* SEARCH %s\r\n' % ' '.join(uids)], 'OK SEARCH completed'
            if command == 'APPEND':
                return [], 'OK [APPENDUID 7 1] APPEND completed'
            return [], 'OK %s completed' % command

    def _exists(self, state):
        """What changed in the open folder since this connection last heard, as Gmail tells every
        connected program: new mail (EXISTS), and read or flags changed by another device (FETCH)."""
        box = state.get('box')
        lines = []
        if box and len(box.uids) != state.get('seen_count'):
            state['seen_count'] = len(box.uids)
            lines.append('* %d EXISTS\r\n' % len(box.uids))
        if box:
            known = state.setdefault('known', {})
            for index, (uid, message) in enumerate(box.uids, 1):
                now = self._flags(box, uid, message)
                if uid in known and known[uid] != now:
                    lines.append('* %d FETCH (UID %d FLAGS %s)\r\n' % (index, uid, now))
                known[uid] = now
        return lines

    def _list(self, command, args):
        pattern = unquote(args.split(' ', 1)[1] if ' ' in args else '*').strip()
        if args.upper().startswith('(SPECIAL-USE)'):
            pattern = '*'
        lines = []
        for name in FOLDERS:
            if pattern not in ('*', '%') and pattern != name and not (pattern.endswith('*') and name.startswith(pattern[:-1])):
                continue
            if pattern == '%' and '/' in name:
                continue
            flags = SPECIAL.get(name, '\\HasNoChildren')
            if command == 'XLIST' and name == 'INBOX':
                flags = '\\HasNoChildren \\Inbox'
            lines.append('* %s (%s) "/" %s\r\n' % ('LIST' if command == 'XLIST' else command, flags, quote(name)))
        return lines

    def _targets(self, box, spec, by_uid):
        found = []
        for index, (uid, message) in enumerate(box.uids, 1):
            number = uid if by_uid else index
            top = box.uids[-1][0] if by_uid and box.uids else len(box.uids)
            for part in spec.split(','):
                low, _, high = part.partition(':')
                low = top if low == '*' else int(low)
                high = low if not high else (top if high == '*' else int(high))
                if min(low, high) <= number <= max(low, high):
                    found.append((index, uid, message))
                    break
        return found

    def _flags(self, box, uid, message):
        flags = set(message.flags)
        if uid in box.deleted:
            flags.add(DELETED)
        return '(' + ' '.join(sorted(flags)) + ')'

    def _fetch(self, box, args, by_uid):
        spec, _, items = args.partition(' ')
        items = items.strip()
        if items.startswith('(') and items.endswith(')'):
            items = items[1:-1]
        wanted = re.findall(r'BODY(?:\.PEEK)?\[[^\]]*\](?:<\d+\.\d+>)?|[A-Z0-9\-\.]+', items.upper())
        lines = []
        for index, uid, message in self._targets(box, spec, by_uid):
            parts = []
            literals = []
            if by_uid and 'UID' not in wanted:
                wanted = ['UID'] + wanted
            for item in wanted:
                if item == 'UID':
                    parts.append('UID %d' % uid)
                elif item == 'FLAGS':
                    parts.append('FLAGS ' + self._flags(box, uid, message))
                elif item == 'RFC822.SIZE':
                    parts.append('RFC822.SIZE %d' % len(message.raw))
                elif item == 'INTERNALDATE':
                    parts.append('INTERNALDATE "%s"' % message.date)
                elif item == 'X-GM-MSGID':
                    parts.append('X-GM-MSGID %d' % message.msgid)
                elif item == 'X-GM-THRID':
                    parts.append('X-GM-THRID %d' % message.thrid)
                elif item == 'X-GM-LABELS':
                    parts.append('X-GM-LABELS (%s)' % ' '.join(quote(label) for label in self.labels(message) if label != box.name))
                elif item == 'MODSEQ':
                    parts.append('MODSEQ (1)')
                elif item == 'BODYSTRUCTURE':
                    body = message.raw.split(b'\r\n\r\n', 1)[1]
                    parts.append('BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" %d %d)' % (
                        len(body), body.count(b'\r\n')))
                elif item.startswith('BODY') or item in ('RFC822', 'RFC822.HEADER', 'RFC822.TEXT'):
                    section = re.search(r'\[([^\]]*)\]', item)
                    section = section.group(1) if section else {'RFC822': '', 'RFC822.HEADER': 'HEADER', 'RFC822.TEXT': 'TEXT'}.get(item, '')
                    head, _, body = message.raw.partition(b'\r\n\r\n')
                    if section.startswith('HEADER.FIELDS'):
                        names = [n.upper() for n in re.findall(r'[A-Z0-9\-]+', section.split('(', 1)[1])] if '(' in section else []
                        keep = []
                        for line in re.split(rb'\r\n(?![ \t])', head):
                            key = line.split(b':', 1)[0].decode('ascii', 'replace').strip().upper()
                            if (key in names) != section.startswith('HEADER.FIELDS.NOT'):
                                keep.append(line)
                        data = b'\r\n'.join(keep) + b'\r\n\r\n'
                    elif section == 'HEADER':
                        data = head + b'\r\n\r\n'
                    elif section == 'TEXT' or section == '1':
                        data = body
                    else:
                        data = message.raw
                    partial = re.search(r'<(\d+)\.(\d+)>', item)
                    name = re.sub(r'\.PEEK', '', item.split('<')[0])
                    if partial:
                        start = int(partial.group(1))
                        data = data[start:start + int(partial.group(2))]
                        name += '<%d>' % start
                    if item.startswith('BODY[') or item.startswith('RFC822') and section == '':
                        if SEEN not in message.flags and not box.name.startswith('[Gmail]/Sent'):
                            message.flags.add(SEEN)   # a fetch without PEEK marks it read, as IMAP says
                    literals.append(len(parts))
                    parts.append((name, data))
            text = b'* %d FETCH (' % index
            pieces = []
            for part in parts:
                if isinstance(part, tuple):
                    pieces.append(part[0].encode() + b' {%d}\r\n' % len(part[1]) + part[1])
                else:
                    pieces.append(part.encode())
            lines.append(text + b' '.join(pieces) + b')\r\n')
        return lines

    def _store(self, box, args, by_uid):
        spec, how, flags = re.match(r'(\S+)\s+([+-]?FLAGS(?:\.SILENT)?)\s+\(?([^)]*)\)?', args, re.I).groups()
        flags = set(flags.split())
        lines = []
        for index, uid, message in self._targets(box, spec, by_uid):
            for flag in flags:
                if flag == DELETED:
                    target = box.deleted
                    if how.startswith('-'):
                        target.discard(uid)
                    elif how.startswith('+') or how.upper().startswith('FLAGS'):
                        target.add(uid)
                    continue
                if how.startswith('-'):
                    message.flags.discard(flag)
                else:
                    message.flags.add(flag)
            if how.upper().startswith('FLAGS'):
                message.flags = {f for f in flags if f != DELETED}
            self.log.append('STORED %s %s %s in %s' % (how, ' '.join(sorted(flags)), message.header_value('Subject'), box.name))
            if '.SILENT' not in how.upper():
                lines.append('* %d FETCH (UID %d FLAGS %s)\r\n' % (index, uid, self._flags(box, uid, message)))
        return lines

    def _copy(self, box, args, by_uid):
        spec, _, target = args.partition(' ')
        name = unquote(target.strip())
        destination = self.boxes.get('INBOX' if name.upper() == 'INBOX' else name)
        if destination is None:
            return [], 'NO [TRYCREATE] No such folder'
        old, new = [], []
        for _, uid, message in self._targets(box, spec, by_uid):
            if not destination.has(message):   # Gmail: a label is there once
                new.append(destination.add(message))
                old.append(uid)
        self.log.append('COPIED %d to %s' % (len(new), destination.name))
        if not new:
            return [], 'OK COPY completed'
        return [], 'OK [COPYUID 7 %s %s] COPY completed' % (','.join(map(str, old)), ','.join(map(str, new)))

    def _expunge(self, box):
        lines = []
        for index in range(len(box.uids), 0, -1):
            uid, message = box.uids[index - 1]
            if uid in box.deleted:
                box.uids.pop(index - 1)
                box.deleted.discard(uid)
                lines.append('* %d EXPUNGE\r\n' % index)
                self.log.append('EXPUNGED %s from %s' % (message.header_value('Subject'), box.name))
        return lines

    # ---- SMTP ----

    def _smtp_client(self, connection):
        reader = connection.makefile('rb')
        send = lambda text: connection.sendall(text.encode('utf-8'))
        send('220 fake.gmail.example ESMTP ready\r\n')
        sender, recipients = '', []
        while True:
            line = reader.readline()
            if not line:
                return
            text = line.decode('utf-8', 'replace').rstrip('\r\n')
            self.log.append('SMTP ' + text[:200])
            verb = text[:4].upper()
            if verb in ('EHLO', 'HELO'):
                send('250-fake.gmail.example\r\n250-8BITMIME\r\n250 SIZE 35882577\r\n')
            elif verb == 'MAIL':
                sender, recipients = text, []
                send('250 OK\r\n')
            elif verb == 'RCPT':
                recipients.append(text)
                send('250 OK\r\n')
            elif verb == 'DATA':
                send('354 Go ahead\r\n')
                data = b''
                while True:
                    chunk = reader.readline()
                    if not chunk or chunk == b'.\r\n':
                        break
                    data += chunk[1:] if chunk.startswith(b'..') else chunk
                with self.lock:
                    self.smtp_messages.append({'from': sender, 'to': recipients, 'raw': data})
                    self.next_id += 1
                    # Gmail files its own copy of what was sent.
                    message = Message(data, self.next_id, self.next_id, flags=(SEEN,))
                    self.boxes['[Gmail]/Sent Mail'].add(message)
                    self.boxes['[Gmail]/All Mail'].add(message)
                send('250 OK message accepted\r\n')
            elif verb == 'QUIT':
                send('221 bye\r\n')
                return
            else:
                send('250 OK\r\n')


def quote(name):
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def unquote(text):
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return text

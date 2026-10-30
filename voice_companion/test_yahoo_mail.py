"""Yahoo Mail answers every mail command the way the other mailboxes do.

The tests drive the real yahoo_mailbox code through the real imaplib and
smtplib, talking over a local socket to small servers that answer the way
imap.mail.yahoo.com and smtp.mail.yahoo.com do: Yahoo's folder names (Inbox,
Sent, Draft, Trash, Bulk, Archive), quoted names, "/" between folder levels,
capabilities, UIDs, literals and error replies. Nothing here touches the
network outside this computer.
"""
import base64
import imaplib
import re
import smtplib
import socket
import ssl
import threading
import time
import unittest
from email.message import EmailMessage
from email.parser import BytesParser
from email import policy
from unittest.mock import patch

from mailbox_access import MailboxError
from mail_voice import MailSession
import yahoo_mailbox
from yahoo_mailbox import YahooMailbox, encode_folder_name

ADDRESS = 'vet@yahoo.com'
APP_PASSWORD = 'abcdwxyzefghstuv'
GREETING_CAPS = ('IMAP4rev1 SASL-IR AUTH=PLAIN AUTH=XOAUTH2 AUTH=OAUTHBEARER ID NAMESPACE '
                 'XYMHIGHESTMODSEQ UIDPLUS LITERAL+ CHILDREN X-MSG-EXT UNSELECT OBJECTID')
LOGIN_CAPS = ('IMAP4rev1 ID MOVE NAMESPACE XYMHIGHESTMODSEQ UIDPLUS LITERAL+ CHILDREN '
              'X-MSG-EXT UNSELECT OBJECTID')
YAHOO_FOLDERS = (('Inbox', r'\HasNoChildren'), ('Sent', r'\HasNoChildren \Sent'),
                 ('Draft', r'\HasNoChildren \Drafts'), ('Trash', r'\HasNoChildren \Trash'),
                 ('Bulk', r'\HasNoChildren \Junk'), ('Archive', r'\HasNoChildren \Archive'))


def message(sender='Sam Lee <sam@example.com>', subject='Hello', body='Hi there.', to=ADDRESS,
            html=None, extra=None, raw=None):
    if raw is not None:
        return raw
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = to
    msg['Subject'] = subject
    msg['Date'] = 'Mon, 05 Oct 2026 10:00:00 +0000'
    msg['Message-ID'] = '<' + str(abs(hash((sender, subject, body)))) + '@example.com>'
    for name, value in (extra or {}).items():
        msg[name] = value
    if html is not None and body is None:
        msg.set_content(html, subtype='html')
    else:
        msg.set_content(body)
        if html is not None:
            msg.add_alternative(html, subtype='html')
    return msg.as_bytes()


class Folder:
    def __init__(self, flags):
        self.flags, self.messages, self.uidnext = flags, [], 1


class YahooIMAPServer:
    """Answers IMAP the way imap.mail.yahoo.com does, on a local socket."""

    def __init__(self, folders=YAHOO_FOLDERS, greeting_caps=GREETING_CAPS, login_caps=LOGIN_CAPS,
                 literal_names=()):
        self.folders = {name: Folder(flags) for name, flags in folders}
        self.greeting_caps, self.login_caps = greeting_caps, login_caps
        self.literal_names = set(literal_names)
        self.password = APP_PASSWORD
        self.log, self.drop_on, self.stall_on, self.refuse = [], {}, set(), {}
        self.connections, self.sockets = 0, []
        self.lock = threading.Lock()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(20)
        self.port = self.listener.getsockname()[1]
        self.closing = False
        threading.Thread(target=self._accept, daemon=True).start()

    # The app calls imaplib.IMAP4_SSL('imap.mail.yahoo.com', 993, ...). The test
    # checks those arguments, then gives the app a real imaplib connection to here.
    def factory(self, host, port, ssl_context=None, timeout=None):
        assert (host, port) == ('imap.mail.yahoo.com', 993), (host, port)
        assert isinstance(ssl_context, ssl.SSLContext) and ssl_context.check_hostname
        assert timeout and timeout <= 30, timeout
        return imaplib.IMAP4('127.0.0.1', self.port, timeout=timeout)

    def close(self):
        self.closing = True
        for sock in [self.listener] + self.sockets:
            try: sock.close()
            except OSError: pass

    def add(self, folder, raw, flags=(), date='05-Oct-2026 10:00:00 +0000'):
        box = self.folders[folder]
        box.messages.append({'uid': box.uidnext, 'flags': set(flags), 'date': date, 'raw': raw})
        box.uidnext += 1
        return box.messages[-1]['uid']

    def commands(self, name):
        return [entry for entry in self.log if entry[1] == name]

    def _accept(self):
        while not self.closing:
            try: sock, _ = self.listener.accept()
            except OSError: return
            self.connections += 1
            self.sockets.append(sock)
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        stream = sock.makefile('rb')
        state = {'selected': None, 'readonly': False, 'auth': False}
        def send(data):
            sock.sendall(data if isinstance(data, bytes) else data.encode('latin-1'))
        try:
            send('* OK [CAPABILITY ' + self.greeting_caps + '] IMAP4rev1 imapgate-1.8.1_01.20166 imap.mail.yahoo.com\r\n')
            while True:
                line = stream.readline()
                if not line:
                    return
                literals = []
                while True:
                    match = re.search(rb'\{(\d+)(\+?)\}\r\n$', line)
                    if not match:
                        break
                    if not match[2]:
                        send('+ Ready for literal data\r\n')
                    literals.append(stream.read(int(match[1])))
                    line = line[:match.start()] + b'\x00' + str(len(literals) - 1).encode() + b'\x00' + stream.readline()
                text = line.rstrip(b'\r\n').decode('latin-1')
                tag, _, rest = text.partition(' ')
                command, _, args = rest.partition(' ')
                command = command.upper()
                if command == 'UID':
                    sub, _, args = args.partition(' ')
                    command = 'UID ' + sub.upper()
                with self.lock:
                    self.log.append((tag, command, args))
                    drop = self.drop_on.get(command, 0)
                    if drop:
                        self.drop_on[command] = drop - 1
                if drop:
                    sock.close()
                    return
                if command in self.stall_on:
                    time.sleep(3)
                    sock.close()
                    return
                if command in self.refuse:
                    send(tag + ' ' + self.refuse[command] + '\r\n')
                    continue
                with self.lock:
                    replies, done = self._answer(command, self._args(args, literals), args, state)
                for reply in replies:
                    send(reply)
                send(tag + ' ' + done + '\r\n')
                if command == 'LOGOUT':
                    sock.close()
                    return
        except OSError:
            return

    @staticmethod
    def _args(text, literals):
        out, i = [], 0
        while i < len(text):
            c = text[i]
            if c == ' ':
                i += 1
            elif c == '"':
                j, value = i + 1, ''
                while text[j] != '"':
                    if text[j] == '\\':
                        j += 1
                    value += text[j]
                    j += 1
                out.append(value)
                i = j + 1
            elif c == '\x00':
                j = text.index('\x00', i + 1)
                out.append(literals[int(text[i + 1:j])])
                i = j + 1
            elif c == '(':
                depth, j = 0, i
                while True:
                    depth += {'(': 1, ')': -1}.get(text[j], 0)
                    if depth == 0:
                        break
                    j += 1
                out.append(('list', text[i + 1:j]))
                i = j + 1
            else:
                j, depth = i, 0
                while j < len(text) and (text[j] != ' ' or depth):
                    depth += {'[': 1, ']': -1}.get(text[j], 0)
                    j += 1
                out.append(text[i:j])
                i = j
        return out

    def _box(self, name):
        name = name if isinstance(name, str) else name.decode('latin-1')
        if name.upper() == 'INBOX':
            name = 'Inbox'
        return name if name in self.folders else None

    @staticmethod
    def _set(spec, values):
        found = []
        biggest = max(values) if values else 0
        for part in spec.split(','):
            low, _, high = part.partition(':')
            low = biggest if low == '*' else int(low)
            high = low if not high else biggest if high == '*' else int(high)
            low, high = min(low, high), max(low, high)
            found.extend(v for v in values if low <= v <= high and v not in found)
        return sorted(found)  # Servers copy and move in UID order.

    def _fetch(self, box, seq, item, items, readonly):
        parts = []
        wanted = items.upper()
        if 'UID' in wanted:
            parts.append('UID ' + str(item['uid']))
        if 'FLAGS' in wanted:
            parts.append('FLAGS (' + ' '.join(sorted(item['flags'])) + ')')
        if 'INTERNALDATE' in wanted:
            parts.append('INTERNALDATE "' + item['date'] + '"')
        if 'RFC822.SIZE' in wanted:
            parts.append('RFC822.SIZE ' + str(len(item['raw'])))
        literal = None
        if 'HEADER.FIELDS' in wanted:
            names = re.search(r'HEADER\.FIELDS \(([^)]*)\)', wanted)[1].split()
            msg = BytesParser(policy=policy.compat32).parsebytes(item['raw'], headersonly=True)
            head, _, _ = item['raw'].partition(b'\n\n') if b'\r\n\r\n' not in item['raw'] else item['raw'].partition(b'\r\n\r\n')
            lines = re.split(rb'\r?\n(?![ \t])', head)
            literal = b''.join(l.rstrip(b'\r\n') + b'\r\n' for l in lines
                               if l.split(b':', 1)[0].decode('latin-1').upper() in names) + b'\r\n'
            prefix = 'BODY[HEADER.FIELDS (' + ' '.join(names) + ')]'
        elif 'BODY.PEEK[]' in wanted or 'BODY[]' in wanted:
            literal = item['raw']
            prefix = 'BODY[]'
            if 'BODY[]' in wanted and 'PEEK' not in wanted and not readonly:
                item['flags'].add('\\Seen')
        head = '* ' + str(seq) + ' FETCH (' + ' '.join(parts)
        if literal is None:
            return [head + ')\r\n']
        return [(head + (' ' if parts else '') + prefix + ' {' + str(len(literal)) + '}\r\n').encode('latin-1')
                + literal + b')\r\n']

    def _answer(self, command, args, raw, state):
        if command == 'CAPABILITY':
            return ['* CAPABILITY ' + (self.login_caps if state['auth'] else self.greeting_caps) + '\r\n'], 'OK CAPABILITY completed'
        if command == 'NOOP':
            return [], 'OK NOOP completed'
        if command == 'LOGOUT':
            return ['* BYE IMAP4rev1 Server logging out\r\n'], 'OK LOGOUT completed'
        if command == 'LOGIN':
            user, password = (a.decode() if isinstance(a, bytes) else a for a in args[:2])
            if user != ADDRESS or password != self.password:
                return [], 'NO [AUTHENTICATIONFAILED] LOGIN Invalid credentials'
            state['auth'] = True
            return [], 'OK [CAPABILITY ' + self.login_caps + '] LOGIN completed'
        if not state['auth']:
            return [], 'BAD [CLIENTBUG] Command not permitted in this state'
        if command == 'LIST':
            replies = []
            for name, folder in self.folders.items():
                if name in self.literal_names:
                    data = name.encode('latin-1')
                    replies.append(('* LIST (' + folder.flags + ') "/" {' + str(len(data)) + '}\r\n').encode() + data + b'\r\n')
                else:
                    replies.append('* LIST (' + folder.flags + ') "/" "' + name + '"\r\n')
            return replies, 'OK LIST completed'
        if command in ('SELECT', 'EXAMINE'):
            name = self._box(args[0])
            if not name:
                state['selected'] = None
                return [], 'NO [TRYCREATE] Mailbox doesn\'t exist'
            state['selected'], state['readonly'] = name, command == 'EXAMINE'
            box = self.folders[name]
            return ['* ' + str(len(box.messages)) + ' EXISTS\r\n', '* 0 RECENT\r\n',
                    '* OK [UIDVALIDITY 1] UIDs valid\r\n', '* OK [UIDNEXT ' + str(box.uidnext) + '] Predicted next UID\r\n',
                    '* FLAGS (\\Answered \\Deleted \\Draft \\Flagged \\Seen $Forwarded $Junk $NotJunk)\r\n'], \
                ('OK [READ-ONLY] EXAMINE completed' if command == 'EXAMINE' else 'OK [READ-WRITE] SELECT completed')
        if command == 'STATUS':
            name = self._box(args[0])
            if not name:
                return [], 'NO [TRYCREATE] Mailbox doesn\'t exist'
            return ['* STATUS "' + name + '" (MESSAGES ' + str(len(self.folders[name].messages)) + ')\r\n'], 'OK STATUS completed'
        if command == 'CREATE':
            name = args[0] if isinstance(args[0], str) else args[0].decode()
            if name in self.folders:
                return [], 'NO [ALREADYEXISTS] Mailbox already exists'
            self.folders[name] = Folder(r'\HasNoChildren')
            return [], 'OK CREATE completed'
        if command == 'RENAME':
            old, new = self._box(args[0]), args[1] if isinstance(args[1], str) else args[1].decode()
            if not old:
                return [], 'NO [NONEXISTENT] Mailbox doesn\'t exist'
            self.folders = {(new if n == old else n): f for n, f in self.folders.items()}
            return [], 'OK RENAME completed'
        if command == 'DELETE':
            name = self._box(args[0])
            if not name:
                return [], 'NO [NONEXISTENT] Mailbox doesn\'t exist'
            del self.folders[name]
            return [], 'OK DELETE completed'
        if command == 'APPEND':
            name = self._box(args[0])
            if not name:
                return [], 'NO [TRYCREATE] Mailbox doesn\'t exist'
            flags = args[1][1].split() if len(args) > 2 and isinstance(args[1], tuple) else []
            uid = self.add(name, args[-1].replace(b'\r\n', b'\n'), flags)
            return [], 'OK [APPENDUID 1 ' + str(uid) + '] APPEND completed'
        box_name = state['selected']
        if not box_name:
            return [], 'BAD [CLIENTBUG] No mailbox selected'
        box = self.folders[box_name]
        uids = [m['uid'] for m in box.messages]
        if command == 'FETCH':
            replies = []
            for seq in self._set(args[0], list(range(1, len(box.messages) + 1))):
                replies.extend(self._fetch(box, seq, box.messages[seq - 1], raw.split(' ', 1)[1], state['readonly']))
            return replies, 'OK FETCH completed'
        if command == 'UID FETCH':
            replies = []
            for uid in self._set(args[0], uids):
                seq = uids.index(uid) + 1
                replies.extend(self._fetch(box, seq, box.messages[seq - 1], 'UID ' + raw.split(' ', 1)[1], state['readonly']))
            return replies, 'OK UID FETCH completed'
        if command == 'UID SEARCH':
            words = [a for a in args if isinstance(a, str)]
            found = uids
            if len(words) >= 3 and words[0].upper() == 'HEADER':
                found = [m['uid'] for m in box.messages
                         if words[2].casefold() in str(BytesParser(policy=policy.compat32).parsebytes(
                             m['raw'], headersonly=True).get(words[1], '')).casefold()]
            return ['* SEARCH' + ''.join(' ' + str(u) for u in found) + '\r\n'], 'OK UID SEARCH completed'
        if command in ('UID MOVE', 'UID COPY'):
            if state['readonly']:
                return [], 'NO [CANNOT] Mailbox is read-only'
            if command == 'UID MOVE' and 'MOVE' not in self.login_caps.split():
                return [], 'BAD [CLIENTBUG] Unknown command'
            target = self._box(args[1])
            if not target:
                return [], 'NO [TRYCREATE] Mailbox doesn\'t exist'
            if target == box_name:
                return [], 'NO [CANNOT] Source and destination are the same'
            chosen = self._set(args[0], uids)
            replies = []
            for uid in chosen:
                item = next(m for m in box.messages if m['uid'] == uid)
                self.add(target, item['raw'], item['flags'] - {'\\Deleted'}, item['date'])
            if command == 'UID MOVE':
                for uid in sorted(chosen, reverse=True):
                    seq = [m['uid'] for m in box.messages].index(uid) + 1
                    box.messages.pop(seq - 1)
                    replies.append('* ' + str(seq) + ' EXPUNGE\r\n')
            return replies, 'OK ' + command + ' completed'
        if command == 'UID STORE':
            if state['readonly']:
                return [], 'NO [CANNOT] Mailbox is read-only'
            flags = args[2][1].split() if isinstance(args[2], tuple) else [args[2]]
            for uid in self._set(args[0], uids):
                item = next(m for m in box.messages if m['uid'] == uid)
                item['flags'] = (item['flags'] | set(flags)) if args[1].startswith('+') else (item['flags'] - set(flags))
            return [], 'OK UID STORE completed'
        if command == 'UID EXPUNGE':
            chosen = set(self._set(args[0], uids))
            replies = []
            for seq in range(len(box.messages), 0, -1):
                item = box.messages[seq - 1]
                if item['uid'] in chosen and '\\Deleted' in item['flags']:
                    box.messages.pop(seq - 1)
                    replies.append('* ' + str(seq) + ' EXPUNGE\r\n')
            return replies, 'OK UID EXPUNGE completed'
        if command == 'EXPUNGE':
            box.messages[:] = [m for m in box.messages if '\\Deleted' not in m['flags']]
            return [], 'OK EXPUNGE completed'
        return [], 'BAD [CLIENTBUG] Unknown command'


class YahooSMTPServer:
    """Answers SMTP the way smtp.mail.yahoo.com does, on a local socket."""

    def __init__(self):
        self.password, self.received, self.refuse_rcpt, self.log = APP_PASSWORD, [], set(), []
        self.data_reply, self.drop_after_data, self.quit_reply = '250 OK , completed', False, '221 Service Closing transmission'
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(5)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def factory(self, host, port, context=None, timeout=None):
        assert (host, port) == ('smtp.mail.yahoo.com', 465), (host, port)
        assert isinstance(context, ssl.SSLContext) and context.check_hostname
        assert timeout and timeout <= 30
        return smtplib.SMTP('127.0.0.1', self.port, timeout=timeout)

    def close(self):
        try: self.listener.close()
        except OSError: pass

    def _accept(self):
        while True:
            try: sock, _ = self.listener.accept()
            except OSError: return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        stream = sock.makefile('rb')
        send = lambda text: sock.sendall((text + '\r\n').encode())
        envelope = {'rcpt': []}
        try:
            send('220 smtp.mail.yahoo.com ESMTP ready')
            while True:
                line = stream.readline().decode().rstrip('\r\n')
                if not line:
                    return
                self.log.append(line.split(' ')[0].upper())
                word = line.split(' ')[0].upper()
                if word in ('EHLO', 'HELO'):
                    send('250-smtp.mail.yahoo.com Hello\r\n250-PIPELINING\r\n250-ENHANCEDSTATUSCODES\r\n250-8BITMIME\r\n250-SIZE 41697280\r\n250 AUTH PLAIN LOGIN XOAUTH2 OAUTHBEARER')
                elif word == 'AUTH':
                    parts = line.split(' ')
                    if parts[1].upper() == 'PLAIN':
                        _, user, password = base64.b64decode(parts[2]).decode().split('\x00')
                    else:
                        send('334 VXNlcm5hbWU6')
                        user = base64.b64decode(stream.readline()).decode()
                        send('334 UGFzc3dvcmQ6')
                        password = base64.b64decode(stream.readline()).decode()
                    if user == ADDRESS and password == self.password:
                        send('235 2.0.0 OK')
                    else:
                        send('535 5.7.0 (#AUTH005) Too many bad auth attempts.')
                elif word == 'MAIL':
                    envelope = {'from': line, 'rcpt': []}
                    send('250 OK , completed')
                elif word == 'RCPT':
                    address = re.search(r'<([^>]*)>', line)[1]
                    if address in self.refuse_rcpt:
                        send('550 5.1.1 <' + address + '>: recipient invalid')
                    else:
                        envelope['rcpt'].append(address)
                        send('250 recipient <' + address + '> ok')
                elif word == 'DATA':
                    send('354 Start Mail. End with CRLF.CRLF')
                    data = b''
                    while True:
                        chunk = stream.readline()
                        if chunk == b'.\r\n':
                            break
                        data += chunk[1:] if chunk.startswith(b'..') else chunk
                    if self.drop_after_data:
                        sock.close()
                        return
                    if self.data_reply.startswith('250'):
                        self.received.append((envelope['rcpt'], data))
                    send(self.data_reply)
                elif word == 'RSET':
                    send('250 OK')
                elif word == 'NOOP':
                    send('250 OK')
                elif word == 'QUIT':
                    send(self.quit_reply)
                    sock.close()
                    return
                else:
                    send('502 5.5.1 Unrecognized command')
        except OSError:
            return


class Draft:
    def __init__(self, recipient='friend@example.com', subject='Lunch', body='See you at noon.',
                 cc=(), bcc=(), context=None):
        from document_editor import Paragraph
        self.recipient, self.subject, self.cc, self.bcc = recipient, subject, list(cc), list(bcc)
        self.paragraphs = [Paragraph(text=body)]
        self.response_context = context or {}


class YahooTestCase(unittest.TestCase):
    def setUp(self):
        self.server = YahooIMAPServer()
        self.addCleanup(self.server.close)

    def mailbox(self, password=APP_PASSWORD, timeout=None):
        box = YahooMailbox(ADDRESS, password, self.server.factory)
        if timeout:
            box.timeout = timeout
        return box



def python312_login(self, user, password):
    """imaplib.IMAP4.login as shipped with Python 3.12, which the Windows build uses:
    it keeps the capability list from before sign-in."""
    typ, dat = self._simple_command('LOGIN', user, self._quote(password))
    if typ != 'OK':
        raise self.error(dat[-1])
    self.state = 'AUTH'
    return typ, dat


class YahooFoldersTests(YahooTestCase):
    def test_folders_use_yahoo_mail_names_and_parse_every_list_form(self):
        server = self.server
        server.folders['Receipts'] = Folder(r'\HasChildren')
        server.folders['Receipts/2026'] = Folder(r'\HasNoChildren')
        server.folders[encode_folder_name('Café notes')] = Folder(r'\HasNoChildren')
        server.folders['Old "quoted"'] = Folder(r'\HasNoChildren')
        server.folders['Hidden'] = Folder(r'\Noselect \HasChildren')
        server.literal_names = {'Old "quoted"'}
        folders = self.mailbox().folders()
        self.assertEqual(folders, [('Inbox', 'Inbox'), ('Sent', 'Sent'), ('Drafts', 'Draft'), ('Trash', 'Trash'),
                                   ('Spam', 'Bulk'), ('Archive', 'Archive'), ('Receipts', 'Receipts'),
                                   ('Receipts/2026', 'Receipts/2026'), ('Café notes', 'Caf&AOk- notes'),
                                   ('Old "quoted"', 'Old "quoted"')])

    def test_folder_changes_with_spaces_and_accents(self):
        box = self.mailbox()
        box.create_folder('Café notes')
        self.assertIn('Caf&AOk- notes', self.server.folders)
        box.create_folder('2026', None, 'Café notes/')
        self.assertIn('Caf&AOk- notes/2026', self.server.folders)
        box.create_folder('Bills')
        box.move_folder('Bills', 'Bills', 'Caf&AOk- notes', 'Café notes/')
        self.assertIn('Caf&AOk- notes/Bills', self.server.folders)
        box.rename_folder('Caf&AOk- notes/Bills', 'Paid bills', 'Café notes/')
        self.assertIn('Caf&AOk- notes/Paid bills', self.server.folders)
        self.assertEqual(box.folder_info('Caf&AOk- notes/Paid bills'), {'messagesTotal': 0})
        box.delete_folder('Caf&AOk- notes/Paid bills')
        self.assertNotIn('Caf&AOk- notes/Paid bills', self.server.folders)


class YahooMessagesTests(YahooTestCase):
    def fill(self, count, folder='Inbox'):
        return [self.server.add(folder, message(subject='Note ' + str(n), body='Body ' + str(n)))
                for n in range(1, count + 1)]

    def test_list_is_newest_first_in_pages_without_listing_the_whole_mailbox(self):
        self.fill(25)
        box = self.mailbox()
        rows, cursor = box.list_messages('inbox')
        self.assertEqual([r['subject'] for r in rows], ['Note ' + str(n) for n in range(25, 15, -1)])
        self.assertEqual(rows[0]['id'], '25')
        # Two messages arrive while the user listens; the next page still starts at Note 15.
        self.server.add('Inbox', message(subject='New 1'))
        self.server.add('Inbox', message(subject='New 2'))
        rows, cursor = box.list_messages('inbox', cursor)
        self.assertEqual([r['subject'] for r in rows], ['Note ' + str(n) for n in range(15, 5, -1)])
        rows, cursor = box.list_messages('inbox', cursor)
        self.assertEqual([r['subject'] for r in rows], ['Note ' + str(n) for n in range(5, 0, -1)])
        self.assertIsNone(cursor)
        # One FETCH per page. A huge mailbox must not be listed UID by UID (imaplib
        # refuses a reply line over 1 MB, about 125,000 messages).
        self.assertEqual(self.server.commands('UID SEARCH'), [])
        self.assertEqual(len(self.server.commands('FETCH')), 3)
        self.assertTrue(all(c[1] == 'EXAMINE' for c in self.server.log if c[1] in ('SELECT', 'EXAMINE')))

    def test_each_header_line_is_said_on_its_own(self):
        # Will, October 2026: Alt+1 to Alt+6, or a question, says one header line.
        from test_email_attachments import StructureServer   # answers BODYSTRUCTURE, as Yahoo does
        server = StructureServer(); self.addCleanup(server.close)
        plans = EmailMessage()
        for name, value in (('From', 'Sam Lee <sam@example.com>'), ('To', 'Me <' + ADDRESS + '>, Pat Kim <pat@example.com>'),
                            ('Cc', 'Lee <lee@example.com>'), ('Subject', 'Plans'), ('Date', 'Mon, 05 Oct 2026 10:00:00 +0000')):
            plans[name] = value
        plans.set_content('Hi there.')
        plans.add_attachment(b'%PDF-1.4 test', maintype='application', subtype='pdf', filename='map.pdf')
        server.add('Inbox', plans.as_bytes(), date='05-Oct-2026 10:00:00 +0000')
        server.add('Inbox', message(subject='Quiet'), date='06-Oct-2026 10:00:00 +0000')
        import tempfile
        factory = lambda *args: YahooMailbox(ADDRESS, APP_PASSWORD, server.factory)
        with patch('mail_voice.account_token', return_value=(ADDRESS, APP_PASSWORD)), patch('mail_voice.YahooMailbox', factory):
            session = MailSession('yahoo', tempfile.mkdtemp(), factory)
            session.process('open inbox')
            self.assertTrue(session.process('read message 2').startswith('Message opened. 1 attachment: map.pdf'))
            self.assertEqual(session.process('who is it from'), 'From: Sam Lee, sam@example.com.')
            self.assertEqual(session.process('who is it to'), 'To: Me and Pat Kim.')
            self.assertEqual(session.process('read the cc'), 'CC: Lee.')
            self.assertEqual(session.process('what is the subject'), 'Subject: Plans.')
            self.assertEqual(session.process('when was it sent'), 'Sent October 5, 2026.')
            self.assertEqual(session.process('how many attachments'), '1 attachment.')
            self.assertEqual(session.process('open message 1'), 'Message opened. Hi there. End of message.')
            self.assertEqual(session.process('how many attachments'), 'No attachments.')
            self.assertEqual(session.process('Read the CC.'), 'No CC.')
            self.assertEqual(session.process('who is it to'), 'To: ' + ADDRESS + '.')

    def test_empty_folder_and_unread_flags(self):
        box = self.mailbox()
        self.assertEqual(box.list_messages('Archive'), ([], None))
        self.server.add('Inbox', message(subject='Read one'), flags=('\\Seen',))
        self.server.add('Inbox', message(subject='Unread one'))
        rows, _ = box.list_messages('inbox')
        self.assertEqual([(r['subject'], r['unread']) for r in rows], [('Unread one', True), ('Read one', False)])

    def test_international_and_damaged_headers_are_spoken_plainly(self):
        self.server.add('Inbox', message(sender='=?iso-8859-1?q?Jos=E9_P=E9rez?= <jose@example.com>',
                                         subject='=?utf-8?b?Q2Fmw6kgbWVudQ==?='), date=' 5-Oct-2026 10:00:00 +0000')
        self.server.add('Inbox', message(raw='From: Zoë <zoe@example.com>\nSubject: Grüße\n\nHallo\n'.encode('utf-8')))
        self.server.add('Inbox', message(raw=b'Subject: =?x-unknown?q?abc?=\n\nNo sender here\n'))
        self.server.add('Inbox', message(raw=b'From: a@example.com\nSubject: Line one\n  folded\n\nBody\n'))
        rows, _ = self.mailbox().list_messages('inbox')
        self.assertEqual([(r['from'], r['subject']) for r in rows],
                         [('a@example.com', 'Line one folded'),
                          ('Unknown sender', 'abc'),
                          ('Zoë <zoe@example.com>', 'Grüße'),
                          ('José Pérez <jose@example.com>', 'Café menu')])
        self.assertEqual(rows[3]['date'], 'October 5, 2026')
        for row in rows:
            for value in row.values():
                if isinstance(value, str):
                    value.encode('utf-8')  # Speech engines fail on leftover surrogates.

    def test_message_bodies_read_the_html_and_keep_the_plain_part(self):
        # The values the removed Gmail reader gave for the same messages (0.2.168).
        samples = [(message(body='Plain words only.'), 'Plain words only.', 'Plain words only.'),
                   (message(body=None, html='<p>Only <b>HTML</b> here.</p><script>alert(1)</script>'),
                    'Only HTML here.', 'Only HTML here.'),
                   (message(body='Plain part.', html='<p>Rich <i>part</i>.</p>'), 'Rich part.', 'Plain part.'),
                   (message(raw=b'From: a@example.com\nContent-Type: text/plain; charset=x-unknown\n\nReadable anyway\n'),
                    'Readable anyway', 'Readable anyway')]
        for raw, spoken, plain in samples:
            uid = self.server.add('Inbox', raw)
            yahoo = self.mailbox().read_message(str(uid), 'inbox')
            self.assertEqual(str(yahoo), spoken)
            self.assertEqual(getattr(yahoo, 'plain', None), plain)
        # Reading never marks the message read.
        self.assertTrue(all('\\Seen' not in m['flags'] for m in self.server.folders['Inbox'].messages))

    def test_missing_and_huge_messages(self):
        box = self.mailbox()
        with self.assertRaisesRegex(MailboxError, 'no longer available. Say list messages'):
            box.read_message('99', 'inbox')
        uid = self.server.add('Inbox', message(body='x' * (6 * 1024 * 1024)))
        with self.assertRaisesRegex(MailboxError, 'too large to read aloud'):
            box.read_message(str(uid), 'inbox')
        # The size is checked first, so six megabytes are never downloaded.
        self.assertFalse([c for c in self.server.log if 'BODY.PEEK[]' in c[2]])

    def test_reply_and_forward_context(self):
        # The values the removed Gmail code gave for the same message (0.2.168).
        raw = message(sender='Sam Lee <sam@example.com>', subject='Plans', body='Meet at nine.',
                      to=ADDRESS + ', pat@example.com', extra={'Cc': 'kim@example.com', 'References': '<a@x>'})
        message_id = str(BytesParser(policy=policy.default).parsebytes(raw)['Message-ID'])
        uid = self.server.add('Inbox', raw)
        expected = {'reply': ('sam@example.com', [], 'Re: Plans', ''),
                    'reply_all': ('sam@example.com', ['pat@example.com', 'kim@example.com'], 'Re: Plans', ''),
                    'forward': ('', [], 'Fwd: Plans', 'Meet at nine.')}
        for action, (recipient, cc, subject, body) in expected.items():
            yahoo = self.mailbox().response_context(str(uid), action, ADDRESS, 'inbox')
            self.assertEqual({key: yahoo[key] for key in ('action', 'source_id', 'message_id', 'references', 'recipient',
                                                          'cc', 'subject', 'original_from', 'original_subject')},
                             {'action': action, 'source_id': str(uid), 'message_id': message_id, 'references': '<a@x>',
                              'recipient': recipient, 'cc': cc, 'subject': subject,
                              'original_from': 'Sam Lee <sam@example.com>', 'original_subject': 'Plans'}, action)
            self.assertEqual(str(yahoo['original_body']), body)
        with self.assertRaises(MailboxError):
            self.mailbox().response_context(str(uid), 'delete', ADDRESS, 'inbox')


class YahooMovingTests(YahooTestCase):
    def test_move_and_delete_work_when_move_is_announced_only_after_sign_in(self):
        # Yahoo's greeting may not list MOVE. Python 3.12 (the Windows build) keeps
        # that list after LOGIN, so Delete and Move must ask again.
        first = self.server.add('Inbox', message(subject='One'))
        second = self.server.add('Inbox', message(subject='Two'))
        with patch.object(imaplib.IMAP4, 'login', python312_login):
            box = self.mailbox()
            box.trash([str(first)], 'inbox')
            box.move([str(second)], 'inbox', 'Archive')
        self.assertEqual(len(self.server.folders['Inbox'].messages), 0)
        self.assertEqual(len(self.server.folders['Trash'].messages), 1)
        self.assertEqual(len(self.server.folders['Archive'].messages), 1)

    def test_delete_moves_to_trash_and_never_erases(self):
        uids = [self.server.add('Inbox', message(subject='N' + str(n))) for n in range(3)]
        box = self.mailbox()
        box.trash([str(u) for u in uids[:2]], 'inbox')
        self.assertEqual([m['raw'] for m in self.server.folders['Trash'].messages],
                         [message(subject='N0'), message(subject='N1')])
        self.assertEqual(len(self.server.commands('UID MOVE')), 1)
        self.assertEqual(self.server.commands('EXPUNGE'), [])
        # Deleting inside Trash leaves the message there, as Gmail does, and says so
        # (it said "1 message moved to Trash" while nothing moved).
        with self.assertRaisesRegex(MailboxError, 'already in Trash') as said:
            box.trash(['1'], 'Trash')
        self.assertTrue(said.exception.unchanged)
        self.assertEqual(len(self.server.folders['Trash'].messages), 2)

    def test_delete_in_trash_is_refused_before_the_question(self):
        # Before: "I heard move this message to Trash. Is that right?", and only after Yes,
        # "This is already in Trash."
        import tempfile
        self.server.add('Trash', message(subject='Old'))
        self.server.add('Inbox', message(subject='New'))
        factory = lambda *args: YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory)
        with patch('mail_voice.account_token', return_value=(ADDRESS, APP_PASSWORD)):
            session = MailSession('yahoo', tempfile.mkdtemp(), factory)
            session.process('go to trash')
            self.assertEqual(session.folder_name, 'Trash')
            self.assertEqual(session.process('delete'),
                             'This is already in Trash. Yahoo empties Trash on its own. Nothing was changed.')
            self.assertIsNone(session.pending)
            session.process('open inbox')
            self.assertIn('Is that right?', session.process('delete'))
        self.assertEqual(len(self.server.folders['Trash'].messages), 1)

    def test_without_move_only_the_chosen_messages_are_removed(self):
        self.server.login_caps = LOGIN_CAPS.replace(' MOVE', '')
        keep = self.server.add('Inbox', message(subject='Flagged elsewhere'), flags=('\\Deleted',))
        chosen = self.server.add('Inbox', message(subject='Chosen'))
        self.mailbox().move([str(chosen)], 'inbox', 'Archive')
        self.assertEqual([m['uid'] for m in self.server.folders['Inbox'].messages], [keep])
        self.assertEqual(len(self.server.folders['Archive'].messages), 1)
        self.assertEqual(self.server.commands('EXPUNGE'), [])
        self.server.login_caps = self.server.greeting_caps = 'IMAP4rev1 AUTH=PLAIN'
        with self.assertRaisesRegex(MailboxError, 'Nothing was changed'):
            self.mailbox().move([str(keep)], 'inbox', 'Archive')

    def test_bad_identifiers_are_refused(self):
        with self.assertRaises(MailboxError):
            self.mailbox().move(['1:100'], 'inbox', 'Archive')
        self.assertEqual(self.server.connections, 0)


class YahooConnectionTests(YahooTestCase):
    def test_wrong_app_password_says_what_to_do(self):
        for password in ('wrong', ''):
            with self.assertRaises(MailboxError) as caught:
                self.mailbox(password).list_messages('inbox')
            self.assertIn('make a new Yahoo app password, then say add account', str(caught.exception))
            self.assertTrue(caught.exception.unchanged)

    def test_app_password_written_in_groups_still_signs_in(self):
        self.assertEqual(self.mailbox('abcd wxyz efgh stuv').list_messages('inbox'), ([], None))

    def test_dropped_connection_while_reading_is_tried_once_more(self):
        uid = self.server.add('Inbox', message(body='Still here.'))
        self.server.drop_on['UID FETCH'] = 1
        self.assertEqual(self.mailbox().read_message(str(uid), 'inbox'), 'Still here.')
        self.server.drop_on['FETCH'] = 2
        with self.assertRaisesRegex(MailboxError, 'did not answer'):
            self.mailbox().list_messages('inbox')

    def test_dropped_connection_before_a_change_is_retried_but_never_after(self):
        uid = self.server.add('Inbox', message())
        self.server.drop_on['SELECT'] = 1
        self.mailbox().move([str(uid)], 'inbox', 'Archive')
        self.assertEqual(len(self.server.folders['Archive'].messages), 1)
        uid = self.server.add('Inbox', message(subject='Second'))
        self.server.drop_on['UID MOVE'] = 1
        with self.assertRaises(MailboxError) as caught:
            self.mailbox().move([str(uid)], 'inbox', 'Archive')
        self.assertIn('check before trying again', str(caught.exception))
        self.assertFalse(caught.exception.unchanged)
        self.assertEqual(len(self.server.commands('UID MOVE')), 2)  # one each move, no repeat

    def test_silent_server_never_freezes_the_app(self):
        self.server.stall_on.add('LIST')
        box = self.mailbox(timeout=1)
        started = time.monotonic()
        with self.assertRaisesRegex(MailboxError, 'did not answer. Check the internet connection'):
            box.folders()
        self.assertLess(time.monotonic() - started, 10)

    def test_server_refusal_is_spoken_plainly(self):
        self.server.refuse['EXAMINE'] = 'NO [UNAVAILABLE] Server error - Please try again later'
        with self.assertRaisesRegex(MailboxError, 'no longer in Yahoo Mail|could not do that right now'):
            self.mailbox().list_messages('inbox')
        self.server.refuse.pop('EXAMINE')
        self.server.refuse['LOGIN'] = 'NO [UNAVAILABLE] LOGIN failure. Server error--please try again after some time.'
        with self.assertRaisesRegex(MailboxError, 'Wait a minute, then try again'):
            self.mailbox().folders()

    def test_every_connection_has_a_time_limit_and_checks_the_certificate(self):
        # YahooIMAPServer.factory asserts imap.mail.yahoo.com:993, a verifying SSL context and a timeout.
        self.mailbox().folders()
        self.assertEqual(yahoo_mailbox.HOST, 'imap.mail.yahoo.com')
        self.assertEqual(yahoo_mailbox.PORT, 993)


class YahooSendingTests(YahooTestCase):
    def setUp(self):
        super().setUp()
        self.smtp = YahooSMTPServer()
        self.addCleanup(self.smtp.close)

    def send(self, draft=None, password=APP_PASSWORD):
        from email_delivery import yahoo_submit
        return yahoo_submit(draft or Draft(), ADDRESS, password, self.smtp.factory,
                            sent_folder=YahooMailbox(ADDRESS, password, self.server.factory).save_sent_copy)

    def test_sent_mail_appears_in_sent_like_gmail(self):
        from email_delivery import delivery_note
        result = self.send(Draft(bcc=['boss@example.com']))
        self.assertEqual(delivery_note(result), '')
        rcpt, data = self.smtp.received[0]
        self.assertEqual(sorted(rcpt), ['boss@example.com', 'friend@example.com'])
        self.assertNotIn(b'Bcc:', data)  # Hidden copies stay hidden from the recipient.
        sent = self.server.folders['Sent'].messages
        self.assertEqual(len(sent), 1)
        self.assertIn('\\Seen', sent[0]['flags'])
        copy = BytesParser(policy=policy.default).parsebytes(sent[0]['raw'])
        self.assertEqual(copy['Bcc'], 'boss@example.com')
        self.assertTrue(copy['Message-ID'] and copy['Date'])
        sent_id = BytesParser(policy=policy.default).parsebytes(data)['Message-ID']
        self.assertEqual(copy['Message-ID'], sent_id)

    def test_no_second_sent_copy_when_yahoo_already_kept_one(self):
        from email_delivery import yahoo_submit
        def yahoo_keeps_copy(raw, message_id):
            self.server.add('Sent', raw)
            return YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory).save_sent_copy(raw, message_id)
        yahoo_submit(Draft(), ADDRESS, APP_PASSWORD, self.smtp.factory, sent_folder=yahoo_keeps_copy)
        self.assertEqual(len(self.server.folders['Sent'].messages), 1)

    def test_sent_copy_failure_never_reports_a_failed_send(self):
        from email_delivery import delivery_note
        self.server.stall_on.add('APPEND')
        self.server.stall_on.add('UID SEARCH')
        from email_delivery import yahoo_submit
        box = YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory)
        box.timeout = 1
        result = yahoo_submit(Draft(), ADDRESS, APP_PASSWORD, self.smtp.factory, sent_folder=box.save_sent_copy)
        self.assertEqual(len(self.smtp.received), 1)
        self.assertIn('copy could not be saved in your Yahoo Sent folder', delivery_note(result))

    def test_reply_threads_and_forward_quotes_the_original(self):
        uid = self.server.add('Inbox', message(sender='Sam <sam@example.com>', subject='Plans', body='Meet at nine.'))
        box = YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory)
        reply = box.response_context(str(uid), 'reply', ADDRESS, 'inbox')
        self.send(Draft(recipient=reply['recipient'], subject=reply['subject'], body='Yes.', context=reply))
        sent = BytesParser(policy=policy.default).parsebytes(self.smtp.received[-1][1])
        self.assertEqual(sent['In-Reply-To'], reply['message_id'])
        self.assertEqual(sent['Subject'], 'Re: Plans')
        forward = box.response_context(str(uid), 'forward', ADDRESS, 'inbox')
        self.send(Draft(recipient='pat@example.com', subject=forward['subject'], body='FYI.', context=forward))
        text = BytesParser(policy=policy.default).parsebytes(self.smtp.received[-1][1]).get_body(('plain',)).get_content()
        text = text.replace('\r\n', '\n')
        self.assertIn('Forwarded message:\nFrom: Sam <sam@example.com>', text)
        self.assertIn('Meet at nine.', text)

    def test_refusals_are_certain_and_say_what_to_do(self):
        from email_delivery import DeliveryError
        with self.assertRaises(DeliveryError) as caught:
            self.send(password='wrongpassword')
        self.assertFalse(caught.exception.uncertain)
        self.assertIn('make a new Yahoo app password, then say add account', str(caught.exception))
        self.smtp.refuse_rcpt.add('friend@example.com')
        with self.assertRaises(DeliveryError) as caught:
            self.send()
        self.assertFalse(caught.exception.uncertain)
        self.assertIn('Yahoo would not send to friend@example.com', str(caught.exception))
        self.smtp.refuse_rcpt.clear()
        self.smtp.data_reply = '554 6.6.0 Error sending message for delivery.'
        with self.assertRaises(DeliveryError) as caught:
            self.send()
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.server.folders['Sent'].messages, [])

    def test_unreachable_server_is_certain_but_a_lost_reply_is_uncertain(self):
        from email_delivery import DeliveryError, yahoo_submit
        def unreachable(host, port, context=None, timeout=None):
            raise ConnectionRefusedError('refused')
        with self.assertRaises(DeliveryError) as caught:
            yahoo_submit(Draft(), ADDRESS, APP_PASSWORD, unreachable)
        self.assertFalse(caught.exception.uncertain)
        self.assertIn('could not reach Yahoo Mail', str(caught.exception))
        self.smtp.drop_after_data = True
        with self.assertRaises(DeliveryError) as caught:
            self.send()
        self.assertTrue(caught.exception.uncertain)

    def test_some_recipients_refused_still_reports_the_send(self):
        from email_delivery import delivery_note
        self.smtp.refuse_rcpt.add('gone@example.com')
        result = self.send(Draft(cc=['gone@example.com']))
        self.assertEqual(len(self.smtp.received), 1)
        self.assertIn('Yahoo did not accept gone@example.com', delivery_note(result))

    def test_a_failed_goodbye_after_acceptance_is_still_a_send(self):
        from email_delivery import delivery_note
        self.smtp.quit_reply = '451 4.3.0 Try later'
        result = self.send()
        self.assertEqual(str(result), 'accepted by Yahoo SMTP')
        self.assertEqual(delivery_note(result), '')


class ReferenceClient:
    """A plain mailbox held in memory, shaped like the removed Gmail client was:
    the same commands through MailSession must give Yahoo's answers."""
    labels = [('Inbox', 'inbox'), ('Sent', 'sent'), ('Drafts', 'drafts'), ('Trash', 'trash'), ('Spam', 'spam'),
              ('Archive', 'archive')]

    def __init__(self, store):
        self.store = store

    def folders(self):
        return list(self.labels)

    def folder_info(self, ident):
        return {'messagesTotal': len(self.store[ident])}

    def list_messages(self, folder_id, cursor=None, limit=10):
        from mailbox_access import message_date
        items = list(reversed(self.store[folder_id]))
        start = int(cursor or 0)
        rows = []
        for ident, raw in items[start:start + limit]:
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            rows.append({'id': ident, 'from': str(msg['From']), 'subject': str(msg['Subject']),
                         'date': message_date('1791194400000'), 'size': len(raw), 'unread': True})
        return rows, (str(start + limit) if len(items) > start + limit else None)

    def read_message(self, message_id, folder_id=None):
        from yahoo_mailbox import message_body
        raw = next(raw for box in self.store.values() for ident, raw in box if ident == message_id)
        return message_body(BytesParser(policy=policy.default).parsebytes(raw))

    def move(self, ids, source_id, destination_id):
        for ident in ids:
            item = next(entry for entry in self.store[source_id] if entry[0] == ident)
            self.store[source_id].remove(item)
            self.store[destination_id].append(item)

    def trash(self, ids):
        for box in [b for b in self.store if b != 'trash']:
            for item in [e for e in self.store[box] if e[0] in ids]:
                self.store[box].remove(item)
                self.store['trash'].append(item)


class SameCommandsSameResultsTests(YahooTestCase):
    """The same spoken commands give the same answers on Yahoo and a plain mailbox."""

    SCRIPT = ('open inbox', 'next message', 'Read message 2.', 'continue reading', 'message details',
              'which folder', 'back to folder', 'next page', 'next message', 'previous page', 'last message',
              'first message', 'select message 1', 'select message 2', 'what is selected', 'clear selection',
              'Delete', 'Yes, please', 'go to trash', 'open message 1', 'go to inbox', 'move to archive', 'yes',
              'go to archive', 'go to spam', 'go to junk', 'go to drafts', 'go to sent', 'back to inbox',
              'select messages 1 through 3', 'delete selected', 'no', 'delete selected', 'yes',
              'next messages', 'go to folder trash', 'go to inbox', 'rename folder Spam to Other',
              'delete folder Trash')

    def run_script(self, provider, factory):
        import tempfile
        folder = tempfile.mkdtemp()
        with patch('mail_voice.account_token', return_value=(ADDRESS, APP_PASSWORD)), \
             patch('mail_voice.YahooMailbox', factory):
            session = MailSession(provider, folder, factory)
            return [session.process(command) for command in self.SCRIPT]

    def test_yahoo_and_a_plain_mailbox_answer_alike(self):
        raws = [message(subject='Note ' + str(n), body='Body of note ' + str(n) + '.') for n in range(1, 16)]
        for raw in raws:
            self.server.add('Inbox', raw, date='05-Oct-2026 10:00:00 +0000')
        self.server.add('Bulk', message(subject='Prize'), date='05-Oct-2026 10:00:00 +0000')
        store = {'inbox': [(str(n), raw) for n, raw in enumerate(raws, 1)], 'sent': [], 'drafts': [], 'trash': [],
                 'spam': [('99', message(subject='Prize'))], 'archive': []}
        def yahoo(*args):
            return YahooMailbox(ADDRESS, APP_PASSWORD, self.server.factory)
        reference = self.run_script('thunderbird', lambda provider, token: ReferenceClient(store))
        yahoo_answers = self.run_script('yahoo', yahoo)
        for command, expected, answer in zip(self.SCRIPT, reference, yahoo_answers):
            if command in ('rename folder Spam to Other', 'delete folder Trash'):
                self.assertIn('cannot be changed', answer)
                continue
            self.assertEqual(expected.casefold(), answer.casefold(), command)
        self.assertEqual(len(self.server.folders['Trash'].messages), 4)
        self.assertEqual(len(self.server.folders['Archive'].messages), 1)



class CompanionYahooTests(YahooTestCase):
    """The whole app with a Yahoo account: inbox screen, reply and send."""

    def setUp(self):
        super().setUp()
        import companion
        from test_core_email_fixes import CompanionTestCase
        self.companion = companion
        helper = CompanionTestCase('setUp')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.said = helper.said
        self.folder = helper.folder
        self.smtp = YahooSMTPServer()
        self.addCleanup(self.smtp.close)
        store = type('Store', (), {'load': lambda s, provider: {'address': ADDRESS, 'app_password': APP_PASSWORD},
                                   'selected': lambda s: ('yahoo', ADDRESS),
                                   'accounts': lambda s: [('yahoo', ADDRESS)]})()
        import email_delivery
        real_submit = email_delivery.yahoo_submit
        self.password = APP_PASSWORD
        patches = [patch.object(companion, 'ProtectedStore', lambda folder: store),
                   patch.object(companion, 'account_token', lambda provider, folder: (ADDRESS, self.password)),
                   patch('mail_voice.account_token', lambda provider, folder: (ADDRESS, self.password)),
                   patch('mail_voice.YahooMailbox', lambda address, token: YahooMailbox(address, token, self.server.factory)),
                   patch.object(email_delivery, 'yahoo_submit',
                                lambda *a, **k: real_submit(*a, smtp_factory=self.smtp.factory, **k)),
                   patch.object(email_delivery, 'yahoo_sent_folder',
                                lambda address, password: YahooMailbox(address, password, self.server.factory).save_sent_copy),
                   patch.object(companion, 'finish_mail_announcement', lambda *a, **k: None),
                   patch.object(companion, 'focus_email_entry', lambda *a, **k: None, create=True)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def open_inbox(self):
        self.companion.mail_session = MailSession('yahoo', self.folder)
        return self.companion.mail_session.process('open inbox')

    def test_reply_and_send_keeps_a_sent_copy(self):
        self.server.add('Inbox', message(sender='Sam Lee <sam@example.com>', subject='Lunch', body='Noon?'))
        self.assertIn('Lunch', self.open_inbox())
        self.assertEqual(self.companion.handle('Read message 1.', 'mailbox'), 'mailbox')
        self.assertEqual(self.companion.handle('Reply', 'mailbox'), 'email_draft')
        self.assertEqual(self.companion.email_draft.recipient, 'sam@example.com')
        self.companion.handle('Noon works for me.', 'email_draft')
        self.companion.handle('send email', 'email_draft', typed=True)
        self.assertIn('Review before sending. From Yahoo, ' + ADDRESS, self.said[-1])
        self.assertEqual(self.companion.handle('Yes, please.', 'email_draft'), 'mailbox')
        self.assertTrue(any(text == 'Email sent successfully.' for text in self.said), self.said[-3:])
        self.assertEqual(len(self.smtp.received), 1)
        sent = self.server.folders['Sent'].messages
        self.assertEqual(len(sent), 1)
        self.assertIn(b'Noon works for me.', sent[0]['raw'])

    def test_send_says_when_the_sent_copy_is_missing(self):
        self.server.stall_on.update({'LIST'})
        yahoo_mailbox_timeout = patch.object(yahoo_mailbox, 'TIMEOUT', 1)
        yahoo_mailbox_timeout.start()
        self.addCleanup(yahoo_mailbox_timeout.stop)
        from email_draft import VoiceEmail
        from document_editor import Paragraph
        draft = VoiceEmail(self.folder / 'Email Drafts', recipient='friend@example.com', subject='Hi',
                           provider='yahoo', compose_step='body')
        draft.paragraphs = [Paragraph('See you soon.')]
        self.companion.email_draft = draft
        self.companion.handle('send email', 'email_draft', typed=True)
        self.companion.handle('yes', 'email_draft')
        self.assertEqual(len(self.smtp.received), 1)
        self.assertTrue(any('Email sent successfully. A copy could not be saved in your Yahoo Sent folder.' in text
                            for text in self.said), self.said[-4:])

    def test_expired_app_password_is_explained_instead_of_a_guess(self):
        self.server.add('Inbox', message(subject='Lunch'))
        self.open_inbox()
        self.companion.handle('Read message 1', 'mailbox')
        self.password = 'revokedpassword1'
        self.companion.handle('Reply', 'mailbox')
        self.assertIn('make a new Yahoo app password, then say add account', self.said[-1])
        self.companion.handle('Delete', 'mailbox')
        self.companion.handle('Yes, please.', 'mailbox')
        self.assertIn('make a new Yahoo app password', self.said[-1])
        self.assertIn('Nothing was changed', self.said[-1])
        self.assertEqual(len(self.server.folders['Inbox'].messages), 1)

    def test_spoken_yes_with_punctuation_deletes_from_the_inbox_screen(self):
        self.server.add('Inbox', message(subject='Keep me'))
        self.server.add('Inbox', message(subject='Old news'))
        self.open_inbox()
        self.companion.handle('Delete.', 'mailbox')
        self.assertIn('Is that right?', self.said[-1])
        self.companion.handle('Yes, please.', 'mailbox')
        self.assertIn('1 message moved to Trash', self.said[-1])
        self.assertEqual([m['raw'] for m in self.server.folders['Trash'].messages], [message(subject='Old news')])


if __name__ == '__main__':
    unittest.main()

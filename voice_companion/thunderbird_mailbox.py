"""Read, organize and send email through Thunderbird, using its own sign-in.

ThunderbirdMailbox has the same methods as the Yahoo mailbox,
so every email screen and voice command works unchanged. It talks
to the Voice Companion Mail Connection add-on through thunderbird_bridge.
"""
from email.utils import getaddresses

from mailbox_access import MailboxError, html_text, message_date, reply_addresses
from thunderbird_bridge import Dropped, NoAnswer, ThunderbirdError, ThunderbirdUnavailable, current

INBOX = 'inbox'   # Voice Companion's name for "this account's inbox"


# Set by the app: starts Thunderbird in the background and waits for its add-on
# (companion.thunderbird_starter). It raises MailboxError with a plain reason
# when Thunderbird cannot connect without the user's yes.
STARTER = None
# Says a short line while a slow Thunderbird step runs (Voice Companion's status speech).
PROGRESS = None
# Saves a different working identity for an address: REMEMBER_IDENTITY(account, identity_id, account_id).
REMEMBER_IDENTITY = None
STILL_SENDING = 'Still sending through Thunderbird. Press Escape to stop waiting.'
SIGN_IN_TO_SEND = ('Microsoft wants you to sign in before Thunderbird can send this message. '
                   'The sign-in page is opening in Voice Companion\'s browser. After you sign in, Thunderbird '
                   'tries to send it. Check the Sent folder before you send it again.')
SIGN_IN_SECONDS = 150   # how long a send listens for Thunderbird's sign-in page
SIGN_IN_CHECK_SECONDS = 2


class SignInNeeded(Exception):
    """Thunderbird asked for a web sign-in (Microsoft or Google) while sending."""
    def __init__(self, url):
        super().__init__(url)
        self.url = url
# Said when the add-on in Thunderbird is older than attachments (it answers "Unsupported mail request.").
UNSUPPORTED = 'Unsupported mail request.'
NEEDS_NEWER_ADDON = ('Attachments need the newer Voice Companion connection in Thunderbird. '
                     'Voice Companion updates it by itself in a moment. Thunderbird restarts once. If you are writing a message in Thunderbird, finish it first. Then try again.')
OLD_ADDON_MARK = ('Marking messages needs the newer Voice Companion connection in Thunderbird. '
                  'Voice Companion updates it by itself in a moment. Thunderbird restarts once. If you are writing a message in Thunderbird, finish it first. Then try again.')
# Thunderbird's importance names, highest first (the add-on passes them as they are).
PRIORITIES = ('highest', 'high', 'normal', 'low', 'lowest')
UPLOAD_PIECE = 512 * 1024      # a file to send goes to Thunderbird in pieces this size (1 MB message limit)
SAVE_PIECE = 1024 * 1024       # an attachment comes back in pieces this size
NOT_CONNECTED = ('Thunderbird is not connected to Voice Companion. '
                 'Say add account and choose Thunderbird, and Voice Companion checks what is missing.')


# Said after a Thunderbird list when Thunderbird could not check the server for new mail
# first, so the user knows the newest messages may be missing and what to do.
MAIL_CHECK_NOTICES = {
    'failed': ('Thunderbird could not reach your mail server, so the newest messages may be missing. '
               'Check your internet connection, then press F5 or say refresh messages to try again.'),
    'offline': ('Thunderbird is set to work offline, so it did not check for new mail. '
                'In Thunderbird, open the File menu and turn off Work Offline.'),
    'slow': ('Thunderbird is still getting your mail, so the newest messages may not be here yet. '
             'A new account can take several minutes. Press F5 or say refresh messages to check again.'),
}


def person(text):
    """(name, address) from one sender or recipient as Thunderbird gives it.

    Thunderbird decodes the header first, so a name with a comma is not quoted:
    "Smith, John <john@example.com>". Read as a list, that is two people, and
    "Smith" is not an address. The address is empty when there is none."""
    import re
    text = ' '.join(str(text or '').split())
    found = re.fullmatch(r'(.*?)\s*<([^<>\s]+@[^<>\s]+)>', text)
    if found:
        return found[1].strip().strip('"').strip(), found[2]
    pairs = [(name, address) for name, address in getaddresses([text]) if '@' in address]
    return pairs[0] if pairs else ('', '')


def connected():
    bridge = current()
    return bridge is not None and bridge.connected.is_set()


def relay():
    """The connection to Thunderbird. Starts Thunderbird first when it is not running."""
    if not connected() and STARTER is not None:
        STARTER()
    bridge = current()
    if bridge is None or not bridge.connected.is_set():
        raise MailboxError(NOT_CONNECTED)
    return bridge


STOPPED = 'Stopped waiting for Thunderbird.'
# Thunderbird is connected but slow (a large calendar, a server that does not answer). It is not
# "not connected": that sent people to add their account again.
NO_ANSWER = 'Thunderbird did not answer in time. Wait a minute, then try again.'


# Requests that change mail, contacts or events. They are never sent a second time by themselves.
CHANGES = frozenset(('move', 'trash', 'createFolder', 'renameFolder', 'moveFolder', 'deleteFolder', 'mark', 'send',
                     'addContact', 'updateContact', 'deleteContact', 'addEvent', 'deleteEvent', 'modifyEvent'))
CHANGE_UNSURE = ('Thunderbird closed before it said whether that was done. Check whether the change is there. '
                 'If it is not, try again.')


def call(method, **params):
    from user_stop import Stopped
    try:
        return relay().request(method, params)
    except Stopped as exc:
        raise MailboxError(STOPPED) from exc
    except NoAnswer as exc:
        raise MailboxError(NO_ANSWER) from exc
    except ThunderbirdUnavailable as exc:
        if isinstance(exc, Dropped) and method in CHANGES:
            # Sent, then Thunderbird closed: it may be done, and asking again could make a second
            # copy or move twice. Say to check instead (sweep, October 31, 2026).
            raise MailboxError(CHANGE_UNSURE) from exc
        # Thunderbird closed after it was connected: start it again once, then repeat the request.
        if STARTER is None or connected():
            raise MailboxError(NOT_CONNECTED) from exc
        try:
            return relay().request(method, params)
        except Stopped as again:
            raise MailboxError(STOPPED) from again
        except NoAnswer as again:
            raise MailboxError(NO_ANSWER) from again
        except ThunderbirdUnavailable as again:
            raise MailboxError(NOT_CONNECTED) from again
        except ThunderbirdError as again:
            raise MailboxError(str(again)) from again
    except ThunderbirdError as exc:
        raise MailboxError(str(exc)) from exc


def accounts():
    """Thunderbird accounts with sending identities, for connecting in Companion."""
    return call('accounts')


READ_LIMIT = 5 * 1024 * 1024   # characters read aloud from one message, as for Yahoo
LONG_MESSAGE = 'This message is very long. Only the first part is read here. Open it in Thunderbird for the rest.'


def readable_parts(parts):
    """A huge message, made small enough to read: (parts, cut).

    Pictures written into the HTML (data: addresses) are never read aloud, so they go
    first. Then the plain text is used, and only if that is still too long is the
    text cut. Reading many megabytes of HTML stopped Voice Companion for minutes."""
    import re
    html, plain = str(parts.get('html') or ''), str(parts.get('plain') or '')
    if len(html) <= READ_LIMIT and len(plain) <= READ_LIMIT:
        return parts, False
    parts = dict(parts)
    if len(html) > READ_LIMIT:
        html = re.sub(r'''(\bsrc\s*=\s*["']?)data:[^"'\s>]*''', r'\1', html, flags=re.I)
    if len(html) > READ_LIMIT and plain:
        html = ''
    cut = False
    if len(html) > READ_LIMIT:
        html, cut = html[:READ_LIMIT], True
    if len(plain) > READ_LIMIT:
        plain, cut = plain[:READ_LIMIT], True
    parts.update(html=html, plain=plain)
    return parts, cut


class ThunderbirdMailbox:
    def __init__(self, address, account):
        self.address = address
        self.account_id = account.get('account_id', '')
        self.identity_id = account.get('identity_id', '')
        self.notice = ''    # why the last list may be missing new mail, or empty

    def _folder(self, folder_id):
        if not folder_id or folder_id in (INBOX, 'INBOX'):
            return call('inbox', accountId=self.account_id)['id']
        return folder_id

    def folders(self):
        return [(item['name'], item['id']) for item in call('folders', accountId=self.account_id)]

    def folder_info(self, folder_id):
        return call('folderInfo', folderId=self._folder(folder_id))

    def list_messages(self, folder_id, cursor=None, limit=10):
        if limit not in (10, 20, 30, 40, 50, 100):
            raise MailboxError('Choose 10, 20, 30, 40, 50, or 100 messages.')
        offset = int(cursor or 0)
        if offset < 0:
            raise MailboxError('Invalid message page.')
        page = call('list', folderId=self._folder(folder_id), offset=offset, limit=limit)
        if offset == 0:
            # Only the first page checks the server; later pages keep what it found.
            self.notice = MAIL_CHECK_NOTICES.get(page.get('mailCheck'), '')
        return self._rows(page)

    def last_page(self, folder_id, limit=10):
        """The page with the oldest messages, in one request: (rows, next cursor, offset)."""
        if limit not in (10, 20, 30, 40, 50, 100):
            raise MailboxError('Choose 10, 20, 30, 40, 50, or 100 messages.')
        page = call('list', folderId=self._folder(folder_id), offset=0, limit=limit, last=True)
        offset = page.get('offset')
        if not isinstance(offset, int) or offset < 0:
            raise MailboxError('Thunderbird could not find the end of this folder. Update Voice Companion, then try again.')
        rows, following = self._rows(page)
        return rows, following, offset

    @staticmethod
    def _rows(page):
        rows = []
        for item in page.get('rows', []):
            name, sender = person(item.get('from', ''))
            rows.append({'id': item['id'], 'from': sender or item.get('from') or 'Unknown sender',
                         'sender_name': name, 'subject': item.get('subject') or '(no subject)',
                         'date': message_date(item.get('date')),
                         'size': item.get('size') if isinstance(item.get('size'), int) else 'unavailable',
                         'unread': bool(item.get('unread')), 'flagged': item.get('flagged') is True,
                         'junk': item.get('junk') is True, 'replied': item.get('replied') is True,
                         'forwarded': item.get('forwarded') is True,
                         'priority': item.get('priority') if item.get('priority') in PRIORITIES else ''})
        following = page.get('next')
        return rows, (str(following) if following is not None else None)

    @staticmethod
    def _message(message_id):
        try:
            return int(message_id)
        except (TypeError, ValueError):
            raise MailboxError('That message is no longer available. Say list messages to refresh.') from None

    def read_message(self, message_id, folder_id=None):
        parts = call('read', messageId=self._message(message_id))
        from email_reader import plain_body
        parts, cut = readable_parts(parts)
        if parts.get('html'):
            body = html_text(parts['html'])
            if parts.get('plain'):
                body.plain = str(plain_body(parts['plain']))
        else:
            body = plain_body(parts.get('plain', ''))
        if cut:
            body = plain_body(str(body) + '\n\n' + LONG_MESSAGE)
        # None: an older add-on, or Thunderbird could not list them.
        found = parts.get('attachments')
        body.attachments = self._attachment_rows(found) if isinstance(found, list) else None
        # Thunderbird marks a message read when it is opened (add-on 1.8.0 and newer).
        body.marked_read = parts.get('read') is True
        return body

    def mark(self, ids, read=None, flagged=None):
        """Mark messages read or unread, flagged or not: {changed, missing}."""
        params = {'ids': [self._message(item) for item in ids]}
        if read is not None:
            params['read'] = bool(read)
        if flagged is not None:
            params['flagged'] = bool(flagged)
        try:
            result = call('mark', **params)
        except MailboxError as exc:
            if 'Unsupported mail request' in str(exc):
                raise MailboxError(OLD_ADDON_MARK) from None
            raise
        result = result if isinstance(result, dict) else {}
        return {'changed': int(result.get('changed') or 0), 'missing': int(result.get('missing') or 0)}

    @staticmethod
    def _attachment_rows(items):
        from mail_attachments import safe_filename
        rows = []
        for item in items or []:
            if not isinstance(item, dict) or not isinstance(item.get('partName'), str) or not item['partName']:
                continue
            size = item.get('size')
            rows.append({'id': item['partName'], 'name': safe_filename(item.get('name'), 'Attachment ' + item['partName']),
                         'size': size if isinstance(size, int) and size >= 0 else None,
                         'type': str(item.get('contentType') or '')})
        return rows

    def attachments(self, message_id, folder_id=None):
        try:
            return self._attachment_rows(call('attachments', messageId=self._message(message_id)))
        except MailboxError as exc:
            if str(exc) == UNSUPPORTED:
                raise MailboxError(NEEDS_NEWER_ADDON, unchanged=True) from exc
            raise

    def attachment_chunks(self, message_id, attachment, folder_id=None):
        """The bytes of one attachment, a piece at a time."""
        import base64
        import binascii
        ident = self._message(message_id)
        part = str(attachment.get('id', ''))
        offset = 0
        while True:
            try:
                piece = call('attachmentChunk', messageId=ident, partName=part, offset=offset, length=SAVE_PIECE)
            except MailboxError as exc:
                if str(exc) == UNSUPPORTED:
                    raise MailboxError(NEEDS_NEWER_ADDON, unchanged=True) from exc
                raise
            try:
                data = base64.b64decode(str(piece.get('data') or ''), validate=True)
            except (binascii.Error, ValueError, AttributeError) as exc:
                raise MailboxError('This attachment is damaged. It could not be saved.', unchanged=True) from exc
            offset += len(data)
            yield data
            if piece.get('done') or not data:
                return

    def attachment_bytes(self, message_id, attachment, folder_id=None):
        return b''.join(self.attachment_chunks(message_id, attachment, folder_id))

    def message_headers(self, message_id, folder_id=None):
        """The people a message went to: {'to': [(name, address)], 'cc': [...]}, from Thunderbird's own header."""
        info = call('context', messageId=self._message(message_id))
        def people(items):
            found = []
            for item in items or []:
                name, address = person(item)
                if name or address or str(item or '').strip():
                    found.append((name or ('' if address else ' '.join(str(item).split())), address))
            return found
        return {'to': people(info.get('recipients')), 'cc': people(info.get('ccList'))}

    def response_context(self, message_id, action, own_address, folder_id=None):
        if action not in ('reply', 'reply_all', 'forward'):
            raise MailboxError('Unknown email response.')
        info = call('context', messageId=self._message(message_id))
        # Reply-To is the raw header, so it is read as a list; the others come decoded.
        sender = ([found for _, found in getaddresses([info['replyTo']])] if info.get('replyTo') else
                  [person(info.get('author', ''))[1]])
        to = [person(item)[1] for item in info.get('recipients', []) or []]
        copied = [person(item)[1] for item in info.get('ccList', []) or []]
        recipients = []
        for found in reply_addresses(action, sender, to, copied, own_address):
            if found.casefold() not in [a.casefold() for a in recipients]:
                recipients.append(found)
        subject = info.get('subject') or '(no subject)'
        prefix = 'Fwd: ' if action == 'forward' else 'Re: '
        if subject.casefold().startswith(prefix.casefold()): prefix = ''
        # Thunderbird quotes or includes the original itself, like Outlook, so
        # no "Forwarded message" block is added by Voice Companion.
        context = {'action': action, 'source_id': message_id, 'message_id': info.get('headerMessageId', ''),
                   'recipient': recipients[0] if action != 'forward' and recipients else '',
                   'cc': recipients[1:] if action == 'reply_all' else [],
                   'subject': prefix + subject, 'original_body': ''}
        if action == 'forward':
            # The original files go with the forward. None: an older add-on that cannot add them.
            try:
                context['original_attachments'] = self.attachments(message_id)
            except MailboxError:
                context['original_attachments'] = None
        return context

    def move(self, ids, source_id, destination_id):
        call('move', messageIds=[self._message(i) for i in ids], folderId=self._folder(destination_id))

    def is_trash(self, folder_id):
        """True for the account's Trash. Thunderbird deletes for good what is deleted there."""
        if not folder_id or folder_id in (INBOX, 'INBOX'):
            return False
        return any(item.get('id') == folder_id and 'trash' in (item.get('specialUse') or [])
                   for item in call('folders', accountId=self.account_id))

    def trash(self, ids, source_id=None):
        call('trash', messageIds=[self._message(i) for i in ids])

    def create_folder(self, name, parent_id=None, parent_path=''):
        result = call('createFolder', parentId=parent_id or '', accountId=self.account_id, name=name)
        return {'name': result['name'], 'id': result['id']}

    def rename_folder(self, folder_id, new_name, parent_path=''):
        return call('renameFolder', folderId=self._folder(folder_id), name=new_name)

    def move_folder(self, folder_id, old_path, destination_id, destination_path, new_name=None):
        return call('moveFolder', folderId=self._folder(folder_id), destinationId=self._folder(destination_id))

    def delete_folder(self, folder_id):
        call('deleteFolder', folderId=self._folder(folder_id))


def submit(draft, account):
    """Send one draft through Thunderbird. Return Thunderbird's message ID."""
    from email_delivery import DeliveryError, message_parts
    from email_formatting import formatted_body
    recipient, subject, body = message_parts(draft)
    context = draft.response_context or {}
    params = {'identityId': account.get('identity_id', ''), 'email': account.get('address', ''), 'to': [recipient], 'cc': list(draft.cc),
              'bcc': list(getattr(draft, 'bcc', [])), 'subject': subject, 'plain': body,
              'html': formatted_body(draft), 'plainOnly': False}  # Thunderbird keeps an account set to plain text
    # Importance and a read receipt, only when asked for (the account's own settings stay otherwise).
    if getattr(draft, 'importance', '') in ('high', 'low'):
        params['priority'] = draft.importance
    if getattr(draft, 'read_receipt', False) is True:
        params['returnReceipt'] = True
    if context.get('action') in ('reply', 'reply_all') and context.get('source_id') is not None:
        params['reply'] = {'messageId': ThunderbirdMailbox._message(context['source_id']),
                           'type': 'replyToAll' if context['action'] == 'reply_all' else 'replyToSender'}
    elif context.get('action') == 'forward' and context.get('source_id') is not None:
        params['forward'] = {'messageId': ThunderbirdMailbox._message(context['source_id'])}
        if isinstance(context.get('original_attachments'), list):
            # The original's files go along, less the ones the person took off.
            # By part, so the right one of two files with the same name is left out (add-on 1.3.1);
            # older add-ons use the names.
            from email_draft import original_split
            dropped = original_split(context)[1]
            params['forward']['drop'] = [str(item.get('name', '')) for item in dropped]
            params['forward']['dropParts'] = [str(item['id']) for item in dropped if item.get('id')]
    from user_stop import Stopped
    import time
    say = PROGRESS
    files = getattr(draft, 'attachments', None)
    files = list(files) if isinstance(files, list) else []
    from mail_attachments import missing, missing_message
    gone = missing(files)
    if gone:
        raise DeliveryError(missing_message(gone), uncertain=False)
    try:
        bridge = relay()
    except MailboxError as exc:
        raise DeliveryError(str(exc), uncertain=False) from exc
    if files:
        params['attachments'] = upload_files(bridge, files, say)
    # Sending can need a sign-in Thunderbird did not need before (Microsoft asks again for
    # sending). Thunderbird would open it where the user cannot reach it, then time out, so
    # Voice Companion takes the page, as it does while an account is added.
    try:
        bridge.request('captureSignIn', {'seconds': SIGN_IN_SECONDS}, timeout=10)
        listening = True
    except Exception:
        listening = False
    last_said = [time.monotonic()]

    def while_sending():
        if say and time.monotonic() - last_said[0] >= 15:
            last_said[0] = time.monotonic(); say(STILL_SENDING)
        if listening:
            try:
                found = bridge.request('takeSignIn', {}, timeout=10)
            except (ThunderbirdError, ThunderbirdUnavailable):
                return
            if isinstance(found, dict) and found.get('url'):
                raise SignInNeeded(found['url'])
    try:
        result = bridge.request('send', params, timeout=120,
                                progress=while_sending if (say or listening) else None,
                                every=SIGN_IN_CHECK_SECONDS if listening else 15)
    except SignInNeeded as exc:
        error = DeliveryError(SIGN_IN_TO_SEND)   # uncertain: Thunderbird sends once signed in
        error.sign_in_url = exc.url
        raise error from None
    except Stopped as exc:
        # Thunderbird may still finish sending; do not invite a duplicate.
        raise DeliveryError('Stopped waiting. Thunderbird may still send this message. '
                            'Check the Sent folder before sending it again.') from exc
    except MailboxError as exc:
        raise DeliveryError(str(exc), uncertain=False) from exc
    except ThunderbirdUnavailable as exc:
        # The request may already be in Thunderbird; do not invite a duplicate.
        raise DeliveryError('Thunderbird stopped answering while sending. Check the Sent folder in Thunderbird before trying again.') from exc
    except ThunderbirdError as exc:
        raise DeliveryError(str(exc), uncertain=False) from exc
    finally:
        if listening:
            try: bridge.request('captureSignIn', {'seconds': 0}, timeout=5)
            except Exception: pass
    result = result or {}
    used = result.get('identityId')
    if used and used != account.get('identity_id') and REMEMBER_IDENTITY is not None:
        # Thunderbird sent from another identity for this address (an account added twice):
        # remember the one that works, so the next message goes there at once.
        try: REMEMBER_IDENTITY(account, used, result.get('accountId') or account.get('account_id', ''))
        except Exception: pass
    return result.get('headerMessageId') or 'accepted by Thunderbird'


def upload_files(bridge, files, say=None):
    """Send each attached file to Thunderbird ahead of the message. Returns the upload ids.

    Nothing is sent by this: a failure here means the email was certainly not sent."""
    import base64
    from email_delivery import DeliveryError
    from mail_attachments import AttachmentError, SLOW_SIZE, read_local
    from user_stop import Stopped
    ids = []
    try:
        if say and sum(item.get('size') or 0 for item in files) > SLOW_SIZE:
            say('Attaching the files. Please wait.')
        for item in files:
            data = read_local(item)
            ident = bridge.request('uploadStart', {'name': item['name'], 'size': len(data)}, timeout=30)['uploadId']
            ids.append(ident)
            for offset in range(0, len(data), UPLOAD_PIECE):
                bridge.request('uploadChunk', {'uploadId': ident, 'offset': offset,
                                               'data': base64.b64encode(data[offset:offset + UPLOAD_PIECE]).decode('ascii')},
                               timeout=60)
        return ids
    except AttachmentError as exc:
        error = DeliveryError(str(exc), uncertain=False)
    except ThunderbirdError as exc:
        error = DeliveryError(NEEDS_NEWER_ADDON if str(exc) == UNSUPPORTED else
                              str(exc) + ' The email was not sent.', uncertain=False)
    except (ThunderbirdUnavailable, Stopped):
        error = DeliveryError('Thunderbird stopped answering while the files were attached. The email was not sent. '
                              'Try again.', uncertain=False)
    except (KeyError, TypeError) as exc:
        error = DeliveryError('Thunderbird did not take the files. The email was not sent. Try again.', uncertain=False)
    for ident in ids:
        try: bridge.request('uploadCancel', {'uploadId': ident}, timeout=5)
        except Exception: pass
    raise error

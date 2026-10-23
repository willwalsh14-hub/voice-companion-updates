"""Read, organize and send email through Thunderbird, using its own sign-in.

ThunderbirdMailbox has the same methods as the Gmail, Outlook and Yahoo
mailboxes, so every email screen and voice command works unchanged. It talks
to the Voice Companion Mail Connection add-on through thunderbird_bridge.
"""
from email.utils import getaddresses

from mailbox_access import MailboxError, html_text, message_date
from thunderbird_bridge import ThunderbirdError, ThunderbirdUnavailable, current

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


def call(method, **params):
    from user_stop import Stopped
    try:
        return relay().request(method, params)
    except Stopped as exc:
        raise MailboxError(STOPPED) from exc
    except ThunderbirdUnavailable as exc:
        # Thunderbird closed after it was connected: start it again once, then repeat the request.
        if STARTER is None or connected():
            raise MailboxError(NOT_CONNECTED) from exc
        try:
            return relay().request(method, params)
        except Stopped as again:
            raise MailboxError(STOPPED) from again
        except ThunderbirdUnavailable as again:
            raise MailboxError(NOT_CONNECTED) from again
        except ThunderbirdError as again:
            raise MailboxError(str(again)) from again
    except ThunderbirdError as exc:
        raise MailboxError(str(exc)) from exc


def accounts():
    """Thunderbird accounts with sending identities, for connecting in Companion."""
    return call('accounts')


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
            (name, sender), = getaddresses([item.get('from', '')]) or [('', '')]
            rows.append({'id': item['id'], 'from': sender or item.get('from') or 'Unknown sender',
                         'sender_name': name, 'subject': item.get('subject') or '(no subject)',
                         'date': message_date(item.get('date')),
                         'size': item.get('size') if isinstance(item.get('size'), int) else 'unavailable',
                         'unread': bool(item.get('unread'))})
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
        if parts.get('html'):
            body = html_text(parts['html'])
            if parts.get('plain'):
                body.plain = str(plain_body(parts['plain']))
            return body
        return plain_body(parts.get('plain', ''))

    def response_context(self, message_id, action, own_address, folder_id=None):
        if action not in ('reply', 'reply_all', 'forward'):
            raise MailboxError('Unknown email response.')
        info = call('context', messageId=self._message(message_id))
        sender = getaddresses([info.get('replyTo') or info.get('author', '')])
        others = getaddresses(list(info.get('recipients', [])) + list(info.get('ccList', []))) if action == 'reply_all' else []
        recipients = []
        for _, found in sender + others:
            if found and found.casefold() != own_address.casefold() and found.casefold() not in [a.casefold() for a in recipients]:
                recipients.append(found)
        subject = info.get('subject') or '(no subject)'
        prefix = 'Fwd: ' if action == 'forward' else 'Re: '
        if subject.casefold().startswith(prefix.casefold()): prefix = ''
        # Thunderbird quotes or includes the original itself, like Outlook, so
        # no "Forwarded message" block is added by Voice Companion.
        return {'action': action, 'source_id': message_id, 'message_id': info.get('headerMessageId', ''),
                'recipient': recipients[0] if action != 'forward' and recipients else '',
                'cc': recipients[1:] if action == 'reply_all' else [],
                'subject': prefix + subject, 'original_body': ''}

    def move(self, ids, source_id, destination_id):
        call('move', messageIds=[self._message(i) for i in ids], folderId=self._folder(destination_id))

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
    if context.get('action') in ('reply', 'reply_all') and context.get('source_id') is not None:
        params['reply'] = {'messageId': ThunderbirdMailbox._message(context['source_id']),
                           'type': 'replyToAll' if context['action'] == 'reply_all' else 'replyToSender'}
    elif context.get('action') == 'forward' and context.get('source_id') is not None:
        params['forward'] = {'messageId': ThunderbirdMailbox._message(context['source_id'])}
    from user_stop import Stopped
    import time
    say = PROGRESS
    try:
        bridge = relay()
    except MailboxError as exc:
        raise DeliveryError(str(exc), uncertain=False) from exc
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

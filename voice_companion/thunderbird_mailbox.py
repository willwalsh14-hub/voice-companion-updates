"""Read, organize and send email through Thunderbird, using its own sign-in.

ThunderbirdMailbox has the same methods as the Gmail, Outlook and Yahoo
mailboxes, so every email screen and voice command works unchanged. It talks
to the Voice Companion Mail Connection add-on through thunderbird_bridge.
"""
from email.utils import getaddresses

from mailbox_access import MailboxError, html_text, message_date
from thunderbird_bridge import ThunderbirdError, ThunderbirdUnavailable, current

INBOX = 'inbox'   # Voice Companion's name for "this account's inbox"


def relay():
    bridge = current()
    if bridge is None or not bridge.connected.is_set():
        raise MailboxError('Thunderbird is not connected. Open Thunderbird and wait a moment, then try again. '
                           'If it still does not connect, ask your helper to check the Voice Companion Mail Connection add-on.')
    return bridge


def call(method, **params):
    try:
        return relay().request(method, params)
    except ThunderbirdUnavailable as exc:
        raise MailboxError(str(exc)) from exc
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
    params = {'identityId': account.get('identity_id', ''), 'to': [recipient], 'cc': list(draft.cc),
              'bcc': list(getattr(draft, 'bcc', [])), 'subject': subject, 'plain': body,
              'html': formatted_body(draft), 'plainOnly': False}  # Thunderbird keeps an account set to plain text
    if context.get('action') in ('reply', 'reply_all') and context.get('source_id') is not None:
        params['reply'] = {'messageId': ThunderbirdMailbox._message(context['source_id']),
                           'type': 'replyToAll' if context['action'] == 'reply_all' else 'replyToSender'}
    elif context.get('action') == 'forward' and context.get('source_id') is not None:
        params['forward'] = {'messageId': ThunderbirdMailbox._message(context['source_id'])}
    try:
        result = relay().request('send', params, timeout=120)
    except MailboxError as exc:
        raise DeliveryError(str(exc), uncertain=False) from exc
    except ThunderbirdUnavailable as exc:
        # The request may already be in Thunderbird; do not invite a duplicate.
        raise DeliveryError('Thunderbird stopped answering while sending. Check the Sent folder in Thunderbird before trying again.') from exc
    except ThunderbirdError as exc:
        raise DeliveryError(str(exc), uncertain=False) from exc
    return (result or {}).get('headerMessageId') or 'accepted by Thunderbird'

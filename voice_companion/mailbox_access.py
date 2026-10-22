"""Small read/organize adapters for Gmail and Microsoft mailboxes.

All URLs are constructed locally. Returned Graph paging links are accepted only
on graph.microsoft.com so an access token cannot be sent to another host.
"""
import base64
import json
import time
import re
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from urllib import parse, request


class MailboxError(Exception):
    """A mail request failed. unchanged=True means nothing in the mailbox changed."""
    def __init__(self, message='', unchanged=False):
        super().__init__(message)
        self.unchanged = unchanged


def message_date(value):
    """Speak a provider delivery date in the computer's local time zone."""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            date = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        elif isinstance(value, str) and len(value) > 10 and value[2] == '-' and value[6] == '-':
            date = datetime.strptime(value, '%d-%b-%Y %H:%M:%S %z')
        elif isinstance(value, str) and re.match(r'\d{4}-\d\d-\d\dT', value):
            # ISO 8601 only: RFC 2822 dates such as "Tue, ..." and "Thu, ..." also contain a T.
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.astimezone().strftime('%B %d, %Y').replace(' 0', ' ')
    except (TypeError, ValueError, OverflowError, OSError):
        return 'unavailable'


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MailboxError('The mail service redirected unexpectedly. No account token was sent to the new address.')


from email_reader import render_html, plain_body


def html_text(source):
    return render_html(source)


def part_text(part):
    """Text of one message part. An unknown character set is read as UTF-8
    instead of hiding the whole message."""
    try:
        return part.get_content()
    except LookupError:
        return (part.get_payload(decode=True) or b'').decode('utf-8', 'replace')


def gmail_body(raw):
    try:
        payload = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
        if len(payload) > 5 * 1024 * 1024:
            raise MailboxError('This message is too large to read aloud.')
        msg = BytesParser(policy=policy.default).parsebytes(payload)
        part = msg.get_body(preferencelist=('html', 'plain'))
        if part is None:
            return ''
        content = part_text(part)
        body=html_text(content) if part.get_content_type() == 'text/html' else plain_body(content)
        plain=msg.get_body(preferencelist=('plain',))
        if plain is not None:body.plain=str(plain_body(part_text(plain)))
        return body
    except (ValueError, TypeError, UnicodeError, LookupError) as exc:
        raise MailboxError('This message could not be read.') from exc


GMAIL_BATCH_URL = 'https://gmail.googleapis.com/batch/gmail/v1'
GMAIL_BATCH_SIZE = 50  # Google advises 50 or fewer calls per batch to avoid rate limits.
GMAIL_BATCH_PAUSE = 3600  # After a failed batch, fetch one message at a time for an hour.
_gmail_batch_paused_until = 0.0
GMAIL_METADATA = '?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date'


class MailboxClient:
    def __init__(self, provider, token, opener=None):
        if provider not in ('gmail', 'outlook'):
            raise MailboxError('That mailbox is not supported.')
        self.provider, self.token = provider, token
        self.opener = opener or request.build_opener(_NoRedirect).open
        self.base = ('https://gmail.googleapis.com/gmail/v1/users/me'
                     if provider == 'gmail' else 'https://graph.microsoft.com/v1.0/me')

    def _call(self, path, method='GET', payload=None, headers=None):
        url = path if path.startswith('https://') else self.base + path
        if not url.startswith(self.base + '/') and not (self.provider == 'outlook'
                and parse.urlsplit(url).scheme == 'https'
                and parse.urlsplit(url).hostname == 'graph.microsoft.com'
                and parse.urlsplit(url).path.startswith('/v1.0/me/')):
            raise MailboxError('The mail service returned an unsafe address.')
        destination=parse.urlsplit(url)
        if destination.username or destination.password or destination.port not in (None,443):
            raise MailboxError('The mail service returned an unsafe address.')
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        outgoing = request.Request(url, data=data, method=method,
                                   headers={'Authorization': 'Bearer ' + self.token,
                                            'Content-Type': 'application/json', **(headers or {})})
        try:
            with self.opener(outgoing, timeout=30) as response:
                body = response.read(6 * 1024 * 1024)
                if response.read(1):
                    raise MailboxError('The mail response is too large.')
                return json.loads(body) if body else {}
        except MailboxError:
            raise
        except Exception as exc:
            if method != 'GET':
                raise MailboxError('The result is uncertain. Check the folder or messages before trying again.') from exc
            raise MailboxError('The mail service could not complete this request. Try again later.') from exc

    def _gmail_batch(self, ids):
        """Message details for up to 50 ids in one request, keyed by id.

        Gmail's batch endpoint takes multipart/mixed with one GET per part and
        answers in parts named <response-ID>, possibly out of order. Parts that
        did not succeed are left out so the caller can retry them alone.
        """
        if not ids:
            return {}
        boundary = 'voice_companion_' + base64.urlsafe_b64encode(__import__('os').urandom(12)).decode().rstrip('=')
        body = ''.join('--' + boundary + '\r\nContent-Type: application/http\r\nContent-ID: <item' + str(n) +
                       '>\r\n\r\nGET /gmail/v1/users/me/messages/' + parse.quote(ident, safe='') + GMAIL_METADATA +
                       '\r\n\r\n' for n, ident in enumerate(ids)) + '--' + boundary + '--\r\n'
        outgoing = request.Request(GMAIL_BATCH_URL, data=body.encode('utf-8'), method='POST',
                                   headers={'Authorization': 'Bearer ' + self.token,
                                            'Content-Type': 'multipart/mixed; boundary=' + boundary})
        global _gmail_batch_paused_until
        # Batching only saves requests. If it fails for any reason, the caller
        # fetches each message alone, as it did before batching, so the inbox
        # still opens.
        try:
            with self.opener(outgoing, timeout=30) as response:
                content = response.read(6 * 1024 * 1024)
                if response.read(1):
                    raise ValueError('The batch reply is too large.')
                content_type = response.headers.get('Content-Type', '')
        except Exception:
            _gmail_batch_paused_until = time.monotonic() + GMAIL_BATCH_PAUSE
            return {}
        if not content_type.lower().startswith('multipart/'):
            _gmail_batch_paused_until = time.monotonic() + GMAIL_BATCH_PAUSE
            return {}
        try:
            reply = BytesParser(policy=policy.default).parsebytes(
                b'Content-Type: ' + content_type.encode('latin-1') + b'\r\n\r\n' + content)
            results = {}
            for part in reply.iter_parts():
                match = re.fullmatch(r'\s*<?response-item(\d+)>?\s*', str(part.get('Content-ID', '')))
                if not match or int(match[1]) >= len(ids):
                    continue
                raw = part.get_payload(decode=True) or b''
                head, _, payload = raw.replace(b'\r\n', b'\n').partition(b'\n\n')
                status = head.split(b'\n', 1)[0].split()
                if len(status) < 2 or status[1] != b'200':
                    continue
                info = json.loads(payload)
                if isinstance(info, dict):
                    results[ids[int(match[1])]] = info
            return results
        except (ValueError, TypeError, UnicodeError, LookupError):
            return {}

    def folders(self):
        if self.provider == 'gmail':
            result = self._call('/labels')
            return [(v['name'], v['id']) for v in result.get('labels', [])]
        found = []
        def collect(parent_id=None, prefix='', depth=0):
            if depth > 8 or len(found) >= 200:
                return
            root = ('/mailFolders' if parent_id is None else
                    '/mailFolders/' + parse.quote(parent_id, safe='') + '/childFolders')
            link = root + '?$top=100&$select=id,displayName,childFolderCount'
            while link and len(found) < 200:
                data = self._call(link)
                for item in data.get('value', []):
                    name = prefix + item['displayName']
                    found.append((name, item['id']))
                    if item.get('childFolderCount', 0):
                        collect(item['id'], name + '/', depth + 1)
                link = data.get('@odata.nextLink')
        collect()
        return found

    def folder_info(self, folder_id):
        ident = parse.quote(folder_id, safe='')
        return self._call(('/labels/' if self.provider == 'gmail' else '/mailFolders/') + ident)

    def create_folder(self, name, parent_id=None, parent_path=''):
        if self.provider == 'gmail':
            return self._call('/labels', 'POST', {'name': parent_path + name})
        path = ('/mailFolders/' + parse.quote(parent_id, safe='') + '/childFolders'
                if parent_id else '/mailFolders')
        return self._call(path, 'POST', {'displayName': name})

    def rename_folder(self, folder_id, new_name, parent_path=''):
        ident = parse.quote(folder_id, safe='')
        if self.provider == 'gmail':
            return self._call('/labels/' + ident, 'PATCH', {'name': parent_path + new_name})
        return self._call('/mailFolders/' + ident, 'PATCH', {'displayName': new_name})

    def move_folder(self, folder_id, old_path, destination_id, destination_path):
        if self.provider == 'gmail':
            name = old_path.rsplit('/', 1)[-1]
            return self.rename_folder(folder_id, name, destination_path)
        return self._call('/mailFolders/' + parse.quote(folder_id, safe='') + '/move',
                          'POST', {'destinationId': destination_id})

    def delete_folder(self, folder_id):
        ident = parse.quote(folder_id, safe='')
        return self._call(('/labels/' if self.provider == 'gmail' else '/mailFolders/') + ident,
                          'DELETE')

    def list_messages(self, folder_id, cursor=None, limit=10):
        if limit not in (10, 20, 30, 40, 50, 100):
            raise MailboxError('Choose 10, 20, 30, 40, 50, or 100 messages.')
        if self.provider == 'gmail':
            args = {'labelIds': folder_id, 'maxResults': limit}
            if cursor:
                args['pageToken'] = cursor
            data = self._call('/messages?' + parse.urlencode(args))
            rows = []
            ids = [item['id'] for item in data.get('messages', [])]
            details = {}
            for start in range(0, len(ids), GMAIL_BATCH_SIZE):
                if time.monotonic() < _gmail_batch_paused_until:
                    break
                details.update(self._gmail_batch(ids[start:start + GMAIL_BATCH_SIZE]))
            for item in data.get('messages', []):
                info = details.get(item['id'])
                if info is None:
                    # A call the batch could not answer is retried alone, as before batching.
                    info = self._call('/messages/' + parse.quote(item['id'], safe='') + GMAIL_METADATA)
                headers = {h['name'].lower(): h['value'] for h in info.get('payload', {}).get('headers', [])}
                rows.append({'id': item['id'], 'from': headers.get('from', 'Unknown sender'),
                             'subject': headers.get('subject', '(no subject)'),
                             'date': message_date(info.get('internalDate') or headers.get('date')),
                             'size': info.get('sizeEstimate','unavailable'),
                             'unread': 'UNREAD' in info.get('labelIds', [])})
            return rows, data.get('nextPageToken')
        path = (cursor or '/mailFolders/' + parse.quote(folder_id, safe='') +
                '/messages?$top=' + str(limit) + '&$select=id,subject,from,isRead,receivedDateTime&$orderby=receivedDateTime%20desc&$expand=singleValueExtendedProperties($filter=id%20eq%20%27Integer%200x0E08%27)')
        data = self._call(path)
        rows = [{'id': m['id'], 'from': m.get('from', {}).get('emailAddress', {}).get('address', 'Unknown sender'), 'sender_name': m.get('from', {}).get('emailAddress', {}).get('name',''),
                 'subject': m.get('subject') or '(no subject)',
                 'date': message_date(m.get('receivedDateTime')),
                 'size': next((int(p['value']) for p in m.get('singleValueExtendedProperties',[]) if p.get('id')=='Integer 0x0E08' and str(p.get('value','')).isdigit()),'unavailable'),
                 'unread': not m.get('isRead', True)}
                for m in data.get('value', [])]
        return rows, data.get('@odata.nextLink')

    def read_message(self, message_id, folder_id=None):
        ident = parse.quote(message_id, safe='')
        if self.provider == 'gmail':
            data = self._call('/messages/' + ident + '?format=raw')
            if data.get('id', message_id) != message_id or 'raw' not in data:
                raise MailboxError('That message is no longer available. Say list messages to refresh.')
            return gmail_body(data['raw'])
        data = self._call('/messages/' + ident + '?$select=id,body',
                          headers={'Prefer': 'outlook.body-content-type="html"'})
        if data.get('id', message_id) != message_id or 'body' not in data:
            raise MailboxError('That message is no longer available. Say list messages to refresh.')
        body = data.get('body', {})
        content = body.get('content', '')
        return html_text(content) if body.get('contentType', '').lower() == 'html' else plain_body(content)

    def response_context(self, message_id, action, own_address, folder_id=None):
        if action not in ('reply', 'reply_all', 'forward'):
            raise MailboxError('Unknown email response.')
        ident = parse.quote(message_id, safe='')
        if self.provider == 'gmail':
            data = self._call('/messages/' + ident + '?format=raw')
            raw = base64.urlsafe_b64decode(data['raw'] + '=' * (-len(data['raw']) % 4))
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
            return {'action': action, 'source_id': message_id, 'thread_id': data.get('threadId', ''),
                    'message_id': msg.get('Message-ID', ''), 'references': msg.get('References', ''),
                    'recipient': recipients[0] if action != 'forward' and recipients else '',
                    'cc': recipients[1:] if action == 'reply_all' else [],
                    'subject': prefix + subject,
                    'original_from': msg.get('From', ''), 'original_subject': subject,
                    'original_body': gmail_body(data['raw']) if action == 'forward' else ''}
        data = self._call('/messages/' + ident + '?$select=id,subject,from,replyTo,toRecipients,ccRecipients')
        def addresses(items):
            return [v.get('emailAddress', {}).get('address', '') for v in items or []]
        sender = addresses(data.get('replyTo') or [data.get('from', {})])
        others = addresses(data.get('toRecipients')) + addresses(data.get('ccRecipients')) if action == 'reply_all' else []
        recipients = []
        for address in sender + others:
            if address and address.casefold() != own_address.casefold() and address.casefold() not in [a.casefold() for a in recipients]:
                recipients.append(address)
        subject = data.get('subject') or '(no subject)'
        prefix = 'Fwd: ' if action == 'forward' else 'Re: '
        if subject.casefold().startswith(prefix.casefold()): prefix = ''
        return {'action': action, 'source_id': message_id,
                'recipient': recipients[0] if action != 'forward' and recipients else '',
                'cc': recipients[1:] if action == 'reply_all' else [],
                'subject': prefix + subject, 'original_body': ''}

    def move(self, ids, source_id, destination_id):
        for message_id in ids:
            ident = parse.quote(message_id, safe='')
            if self.provider == 'gmail':
                state = self._call('/messages/' + ident + '?format=minimal')
                labels = set(state.get('labelIds', []))
                remove = ([source_id] if source_id in labels and source_id != destination_id else [])
                if destination_id != 'INBOX' and 'INBOX' in labels and 'INBOX' not in remove:
                    remove.append('INBOX')
                self._call('/messages/' + ident + '/modify', 'POST',
                           {'addLabelIds': [destination_id], 'removeLabelIds': remove})
            else:
                self._call('/messages/' + ident + '/move', 'POST', {'destinationId': destination_id})

    def trash(self, ids):
        for message_id in ids:
            ident = parse.quote(message_id, safe='')
            if self.provider == 'gmail':
                self._call('/messages/' + ident + '/trash', 'POST', {})
            else:
                self._call('/messages/' + ident + '/move', 'POST', {'destinationId': 'deleteditems'})

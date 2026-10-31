"""Voice state for a single connected mailbox; no persistent mail cache."""
from list_announcements import name_first
from menu_navigation import menu_label
import re
import traceback
from pathlib import Path

from mail_accounts import AccountError, account_token
from mailbox_access import MailboxError
from yahoo_mailbox import YahooMailbox
from reading_navigation import ReadingCursor, reading_request
from storage import write_text_atomic

NUMBER_WORDS = {'one': 1, 'first': 1, 'two': 2, 'second': 2,
                'three': 3, 'third': 3, 'four': 4, 'fourth': 4,
                'five': 5, 'fifth': 5, 'six': 6, 'sixth': 6,
                'seven': 7, 'seventh': 7, 'eight': 8, 'eighth': 8,
                'nine': 9, 'ninth': 9, 'ten': 10, 'tenth': 10}
# A page holds up to 100 messages, and the recognizer writes numbers as words:
# "read message fifteen", "go to message twenty-one" (sweep, October 30, 2026).
NUMBER_WORDS.update(zip('eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(11, 20)))
for _tens, _name in enumerate('twenty thirty forty fifty sixty seventy eighty ninety'.split(), 2):
    NUMBER_WORDS[_name] = _tens * 10
    for _one, _word in enumerate('one two three four five six seven eight nine'.split(), 1):
        NUMBER_WORDS[_name + ' ' + _word] = NUMBER_WORDS[_name + '-' + _word] = _tens * 10 + _one
NUMBER_WORDS.update({'one hundred': 100, 'a hundred': 100})
# Longest first, so "twenty one" is never read as "twenty".
NUMBER = r'(?:\d+|' + '|'.join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r')'
MAIL_SIZES = (10, 20, 30, 40, 50, 100, 1000)
# "All": pages of 10. The arrows stop at the end of a page, as in every list.
ALL_MESSAGES = 'All messages, 10 at a time. For more, press Page Down, or say next page.'
# Commands about "the message you are on". In an empty folder each says the list is empty.
EMPTY_LIST_COMMANDS = ('next', 'previous', 'next message', 'previous message', 'first message', 'last message',
                       'start of list', 'beginning of list', 'end of list', 'first message on page',
                       'last message on page', 'newest message', 'first message in folder', 'oldest message',
                       'last message in folder', 'message details', 'who is this from', 'read message details',
                       'open current message', 'read current message', 'repeat message', 'read message',
                       'open message', 'open that', 'read that', 'open it', 'read email', 'open email',
                       'that one', 'okay', 'ok', 'confirm', 'confirm that', 'select all messages',
                       'toggle message selection', 'extend selection next', 'extend selection previous',
                       'select', 'mark', 'select message', 'mark message', 'select this message', 'mark this message',
                       'unmark message', 'deselect message', 'unselect message', 'delete', 'delete message',
                       'delete this message', 'move', 'move to folder', 'move message', 'move this message',
                       'move message to folder')


# Spoken questions for one line of the message's header (Will, October 2026). Alt+1 to Alt+6
# ask the same in the message list and in an open message (input_parity.MAIL_HEADER_KEYS).
HEADER_REQUESTS = {
    'from': ('who is it from', 'who sent this', 'who sent it', 'who sent the message', 'who sent this message',
             'read from', 'read the from line', 'read sender', 'read the sender', 'read who it is from'),
    'to': ('who is it to', 'who is it for', 'who was it sent to', 'read to', 'read the to line',
           'read recipients', 'read the recipients'),
    'cc': ('read the cc', 'read cc', 'read the cc line', 'who was copied', 'who is copied', 'who got a copy'),
    'subject': ('what is the subject', "what's the subject", 'read the subject', 'read subject', 'what is it about'),
    'date': ('when was it sent', 'when was this sent', 'read the date', 'read date', 'read the date sent',
             'date sent', 'when did it come'),
    'attachments': ('how many attachments', 'are there attachments', 'any attachments', 'read the attachments count',
                    'attachment count'),
}
HEADER_COMMANDS = {phrase: name for name, phrases in HEADER_REQUESTS.items() for phrase in phrases}

# Read, unread and flags (Will, October 30, 2026). "Mark" alone and "mark message 3" select
# messages; these name what to mark them as. Q and X are their keys (input_parity.SCREEN_KEYS).
STATUS_PHRASES = {
    ('read', True): ('mark as read', 'mark read', 'mark message as read', 'mark this message as read', 'mark it as read',
                     'mark it read', 'mark email as read', 'mark this email as read', 'mark messages as read',
                     'mark selected as read', 'mark the message as read'),
    ('read', False): ('mark as unread', 'mark unread', 'mark message as unread', 'mark this message as unread',
                      'mark it as unread', 'mark it unread', 'mark email as unread', 'mark this email as unread',
                      'mark messages as unread', 'mark selected as unread', 'mark the message as unread',
                      'mark as not read', 'keep as unread', 'keep unread'),
    ('read', None): ('mark as read or unread', 'mark read or unread', 'toggle read', 'read or unread', 'toggle read status'),
    ('flagged', True): ('flag', 'flag message', 'flag this message', 'flag it', 'flag email', 'flag this email',
                        'flag messages', 'flag the message', 'star', 'star message', 'star this message', 'add a flag', 'add flag'),
    ('flagged', False): ('unflag', 'unflag message', 'unflag this message', 'unflag it', 'remove flag', 'remove the flag',
                         'clear flag', 'clear the flag', 'unstar', 'unstar message', 'remove star'),
    ('flagged', None): ('flag or unflag', 'flag or unflag message', 'toggle flag', 'toggle the flag'),
}
STATUS_CHANGES = {phrase: key for key, phrases in STATUS_PHRASES.items() for phrase in phrases}
STATUS_QUESTIONS = ('message status', 'what is the status', "what's the status", 'read the status', 'read status',
                    'is it read', 'is this read', 'is it unread', 'is it flagged', 'is this flagged', 'status')
MARK_NEEDS_THUNDERBIRD = ('This account cannot mark messages in Voice Companion yet. '
                          'Add it in Thunderbird to mark messages read, unread or flagged.')
IMPORTANCE_WORDS = {'highest': 'highest importance', 'high': 'high importance', 'low': 'low importance',
                    'lowest': 'lowest importance'}


UNCERTAIN_CHANGE = ('Some messages may have moved. Say list messages to check the folder before you try again.')


def status_words(row, full=False):
    """'Unread, flagged, replied, high importance' for a message row; '' when there is nothing to say.
    full: also says Read, so a question about the message always has an answer."""
    words = []
    if row.get('unread'):
        words.append('unread')
    elif full:
        words.append('read')
    for name in ('flagged', 'replied', 'forwarded'):
        if row.get(name):
            words.append(name)
    if row.get('priority') in IMPORTANCE_WORDS:
        words.append(IMPORTANCE_WORDS[row['priority']])
    if full and not row.get('flagged'):
        words.append('not flagged')
    text = ', '.join(words)
    return text[:1].upper() + text[1:]

# Everyday words for the inbox's own commands (sweep, October 2026): a new user says
# "next email", "close message" or "check my email", not the command names.
EVERYDAY_COMMANDS = {
    'next email': 'next message', 'previous email': 'previous message',
    'read this message': 'read current message', 'open this message': 'open current message',
    'read this email': 'read current message', 'open this email': 'open current message',
    'read the message': 'read current message', 'open the message': 'open current message',
    'read the email': 'read current message', 'open the email': 'open current message',
    'close message': 'back to folder', 'close this message': 'back to folder', 'close the message': 'back to folder',
    'close email': 'back to folder', 'close this email': 'back to folder', 'close the email': 'back to folder',
    'check email': 'refresh messages', 'check my email': 'refresh messages', 'check mail': 'refresh messages',
    'check my mail': 'refresh messages', 'check for new mail': 'refresh messages', 'check for new email': 'refresh messages',
    'check for new messages': 'refresh messages', 'check for mail': 'refresh messages', 'refresh': 'refresh messages',
    'refresh inbox': 'refresh messages', 'refresh the inbox': 'refresh messages', 'refresh the list': 'refresh messages',
    'what folder am i in': 'which folder', 'which folder am i in': 'which folder', 'what folder is this': 'which folder',
    'go to message list': 'back to folder', 'go to the message list': 'back to folder',
    'back to the message list': 'back to folder', 'back to message list': 'back to folder', 'back to the list': 'back to folder',
}
INBOX_WORDS = re.compile(r'(?:open|go to|back to|return to) (?:the |my )?inbox(?: folder)?')


def header_request(text):
    """'from', 'to', 'cc', 'subject', 'date', 'attachments', or None for other words."""
    command = ' '.join(re.sub(r"[^a-z' ]", ' ', str(text).casefold()).split())
    return HEADER_COMMANDS.get(command)


def plural(count, word):
    """'1 message', '2 messages'."""
    return str(count) + ' ' + word + ('' if count == 1 else 's')


def spoken_people(people):
    """'Jane Smith and Bob Jones': names, or the address when a person has no name."""
    from mail_attachments import joined
    return joined((name or address) for name, address in people)


def sender_address(row):
    from email.utils import getaddresses
    candidates=getaddresses([row.get('sender_address') or row.get('from','')])
    return next((address for _,address in candidates if '@' in address),'')

def sender_name(row):
    from email.utils import parseaddr
    name,address=parseaddr(row.get('from',''))
    return row.get('sender_name') or name or (row.get('from','') if '@' not in row.get('from','') else 'Sender name unavailable') or 'Unknown sender'

class MailSession:
    def __init__(self, provider, folder, client_factory=None, progress=None):
        """provider is 'thunderbird' or 'yahoo'. client_factory(provider, token), when
        given, makes the mailbox instead (tests use a stand-in mail server)."""
        self.provider, self.folder = provider, folder
        self.client_factory = client_factory
        self.progress = progress
        self.folders = []
        self.folder_name = 'Inbox'
        self.folder_id = 'inbox'
        self.rows = []
        self.next_cursor = None
        self.page_cursor = None
        self.previous_cursors = []
        self.current = None
        self.view = 'folder'
        self.body_text = ''
        self.body_offset = 0
        self.reading = ReadingCursor()
        self.selected = set()
        self.marked_ids = {}
        self.pending = None
        self.folder_picker = None
        self.folder_choice = None
        self.list_size = 10
        self.all_messages = False
        # The open message's files ([] none, None unknown) and the (id, folder) they belong to.
        self.attachments = None
        self.attachments_for = None
        try:
            setting = (Path(folder) / 'email-list-size.txt').read_text(encoding='utf-8').strip()
            self.all_messages = setting == 'all'
            if setting.isdigit() and int(setting) in MAIL_SIZES:
                self.list_size = int(setting)
        except OSError:
            pass

    def client(self):
        address, token = account_token(self.provider, self.folder)
        if self.client_factory is not None:
            return self.client_factory(self.provider, token)
        if self.provider == 'yahoo':
            return YahooMailbox(address, token)
        if self.provider == 'thunderbird':
            from thunderbird_mailbox import ThunderbirdMailbox
            return ThunderbirdMailbox(address, token)
        raise AccountError('Email works through Thunderbird or Yahoo. Say add account to connect one.')

    def list_folders(self):
        return self._choose_folder('open')

    def _find_folder(self, spoken):
        name = spoken.strip().replace(' slash ', '/').casefold()
        if not self.folders:
            self.folders = self.client().folders()
        exact = [(title, ident) for title, ident in self.folders if title.casefold() == name]
        matches = exact or [(title, ident) for title, ident in self.folders
                            if title.rsplit('/', 1)[-1].casefold() == name]
        if len(matches) != 1:
            raise MailboxError('That folder name is missing or ambiguous. Say list folders and use the full path.')
        return matches[0]

    @staticmethod
    def _folder_name(name):
        name = name.strip()
        if not name or len(name) > 80 or any(c in name for c in '\r\n/\\') or name in ('.', '..'):
            raise MailboxError('Use a short folder name without slashes.')
        return name

    def _mutable_folder(self, title, ident):
        if title.casefold() in ('inbox', 'sent', 'sent items', 'drafts', 'draft', 'trash',
                                'deleted items', 'junk', 'junk email', 'spam', 'bulk', 'all mail', 'archive'):
            raise MailboxError('That standard folder cannot be changed by voice.')
        if self.provider == 'yahoo' and any(path.startswith(title + '/') for path, _ in self.folders):
            raise MailboxError('This folder has child folders. Move the children first.')

    def _parent_allowed(self, ident):
        """Any folder may hold a new folder; the mail service refuses one it cannot."""

    @staticmethod
    def _spoken_folder(title):
        if '/' not in title:
            return title
        parent, leaf = title.rsplit('/', 1)
        return leaf + ' inside of ' + parent.replace('/', ', inside of ')

    def _choose_folder(self, action, detail=None, candidates=None, ambiguous_name=None):
        folders = self.client().folders()
        if not folders:
            return 'No folders were returned by this account.'
        self.folders = folders
        if candidates is not None:
            folders = [item for item in folders if item in candidates]
            if not folders:
                return 'Those folders changed. Say go to folder and try again.'
        self.folder_picker = {'action': action, 'detail': detail, 'folders': folders,
                              'index': 0, 'ambiguous_name': ambiguous_name}
        verb = {'delete': 'delete it', 'message_move': 'move the messages there',
                'move_current': 'move this message there', 'rename': 'rename it',
                'move_source': 'move it', 'move_destination': 'move the folder there',
                'create_parent': 'put the new folder inside it'}.get(action, 'open it')
        if ambiguous_name:
            return ('I see ' + str(len(folders)) + ' folders named ' + ambiguous_name +
                    '. First choice: ' + menu_label(self._spoken_folder(folders[0][0])) +
                    '. Say next or previous to hear another, then that one to ' + verb + '.')
        return ('Found '+plural(len(folders), 'folder')+'. Folder 1: '+menu_label(self._spoken_folder(folders[0][0]))+
                '. Say next or previous, then that one to ' + verb + '. Say cancel to leave this list.')

    def _open_named_folder(self, spoken):
        self.folders = self.client().folders()
        name = spoken.strip().replace(' slash ', '/').casefold()
        matches = self._named_folders(name)
        # "Go to the trash", "go to my sent folder": the name without the everyday words.
        plain = re.sub(r' folder$', '', re.sub(r'^(?:the|my) ', '', name)).strip()
        if not matches and plain and plain != name:
            matches = self._named_folders(plain)
        if not matches:
            return 'I could not find that folder. Say go to folder to hear the list.'
        if len(matches) > 1:
            return self._choose_folder('open', candidates=matches, ambiguous_name=spoken)
        self.folder_name, self.folder_id = matches[0]
        self.previous_cursors.clear()
        self._clear_marks()
        return self.list_messages()

    def _named_folders(self, name):
        """The folders a spoken name means: a full place, the last part of one, or a standard folder."""
        if '/' in name:
            matches = [item for item in self.folders if item[0].casefold() == name]
        else:
            matches = [item for item in self.folders if item[0].rsplit('/', 1)[-1].casefold() == name]
        if not matches and name in ('sent', 'sent mail', 'sent items'):
            matches = [item for item in self.folders if item[0].casefold() in ('sent', 'sent mail', 'sent items')
                       or item[1].casefold() in ('sent', 'sentitems')]
        if not matches:
            # Standard folders have different IDs by mail service; Yahoo calls spam Bulk.
            spam = ('spam', 'junkemail', 'bulk')
            standard = {'drafts': ('draft', 'drafts'), 'draft': ('draft', 'drafts'),
                        'trash': ('trash', 'deleteditems'), 'deleted items': ('trash', 'deleteditems'),
                        'spam': spam, 'junk': spam, 'junk mail': spam, 'junk email': spam, 'bulk': spam,
                        'bulk mail': spam, 'sent': ('sent', 'sentitems')}.get(name, ())
            matches = [item for item in self.folders if item[1].casefold() in standard]
        return matches

    def _clear_marks(self):
        self.selected.clear()
        self.marked_ids.clear()

    def _mark(self, number):
        row = self.rows[number - 1]
        self.selected.add(number)
        self.marked_ids[row['id']] = self._summary(number)

    def _selected_count(self):
        """'2 selected.' after every mark and unmark, by key or voice; 'None selected.' when none are."""
        return (str(len(self.marked_ids)) + ' selected.') if self.marked_ids else 'None selected.'

    def _unmark(self, number):
        self.selected.discard(number)
        self.marked_ids.pop(self.rows[number - 1]['id'], None)

    def _folder_pick(self, command):
        picker = self.folder_picker
        verb = {'delete': 'delete it', 'message_move': 'move the messages there',
                'move_current': 'move this message there', 'rename': 'rename it',
                'move_source': 'move it', 'move_destination': 'move the folder there',
                'create_parent': 'put the new folder inside it'}.get(picker['action'], 'open it')
        if picker['ambiguous_name']:
            choices = []
            for index, (title, _) in enumerate(picker['folders']):
                label = self._spoken_folder(title).casefold()
                parent = title.rsplit('/', 1)[0].casefold() if '/' in title else ''
                phrases = {label, 'the ' + label, str(index + 1),
                           ('first' if index == 0 else 'second' if index == 1 else 'third') + ' folder'}
                if parent:
                    phrases.update({'inside ' + parent, 'inside of ' + parent,
                                    'the one inside ' + parent, 'the one inside of ' + parent})
                if command in phrases:
                    choices.append(index)
            if len(choices) == 1:
                picker['index'] = choices[0]
                return self._folder_pick('okay')
        if command in ('next folder', 'move down', 'down', 'next', 'previous folder', 'move up', 'up', 'previous'):
            step = 1 if command in ('next folder', 'move down', 'down', 'next') else -1
            new = picker['index'] + step
            if not 0 <= new < len(picker['folders']):
                # Said as in every other list (Start of list, End of list).
                index = picker['index']
                return (('End of list. ' if step > 0 else 'Start of list. ') + 'Folder ' + str(index + 1) + ' of ' +
                        str(len(picker['folders'])) + ': ' + self._spoken_folder(picker['folders'][index][0]) + '.')
            picker['index'] = new
            return name_first('Folder ' + str(new + 1) + ' of ' + str(len(picker['folders'])) + ': ' + self._spoken_folder(picker['folders'][new][0]) + '. Say that one to ' + verb + '.')
        if command in ('current folder', 'repeat folder'):
            return 'Selected folder: ' + self._spoken_folder(picker['folders'][picker['index']][0]) + '. Say that one to ' + verb + '.'
        if command in ('okay', 'ok', 'select folder', 'that one', 'confirm', 'confirm that'):
            name, ident = picker['folders'][picker['index']]
            self.folder_picker = None
            self.folders = self.client().folders()
            if (name, ident) not in self.folders:
                return 'The folder list changed. Say go to folder and choose again.'
            action, detail = picker['action'], picker['detail']
            if action == 'open':
                self.folder_name, self.folder_id = name, ident
                self.previous_cursors.clear()
                self._clear_marks()
                return self.list_messages()
            if action in ('message_move', 'move_current'):
                return self._prepare_move(detail, name, ident)
            if action == 'delete': return self.process('delete folder ' + name)
            if action == 'rename':
                self.folder_choice = ('rename', name, ident)
                return 'Chosen ' + name + '. Say rename to followed by the new name.'
            if action == 'move_source':
                self.folder_choice = ('move', name, ident)
                return self._choose_folder('move_destination', name)
            if action == 'move_destination':
                self.folder_choice = None
                return self.process('move folder ' + detail + ' into ' + name)
            if action == 'create_parent': return self.process('create folder ' + detail + ' inside ' + name)
        return None

    def _current_ids(self):
        if self.current is None or not 1 <= self.current <= len(self.rows):
            raise MailboxError('There is no highlighted message. Say list messages first.')
        return [self.rows[self.current - 1]['id']]

    def _selected_ids(self):
        if not self.marked_ids:
            raise MailboxError('Mark messages as you browse, or say select message followed by a number.')
        return list(self.marked_ids)

    def _action_ids(self):
        return self._selected_ids() if self.marked_ids else self._current_ids()

    def _prepare_move(self, ids, title, ident):
        if ident == self.folder_id or self.folder_id in ('inbox', 'INBOX') and title.casefold() == 'inbox':
            # Never "1 message moved." when it stays where it is.
            return ('This is' if len(ids) == 1 else 'These are') + ' already in ' + self._spoken_folder(title) + '. Nothing was changed.'
        self.pending = ('move', ids, ident)
        target = ('the marked message' if ids[0] in self.marked_ids else 'this message') if len(ids) == 1 else str(len(ids)) + ' marked messages'
        return 'I heard move ' + target + ' to ' + self._spoken_folder(title) + '. Is that right? Say yes or no.'

    def _prepare_delete(self, ids):
        in_trash = getattr(self.client(), 'is_trash', None)
        try:
            in_trash = callable(in_trash) and in_trash(self.folder_id)
        except (MailboxError, AccountError, OSError):
            in_trash = False   # the delete itself checks again, and says what went wrong
        if in_trash:
            # Never "moved to Trash" when nothing moves; in Thunderbird it would be deleted for good.
            return (('This is' if len(ids) == 1 else 'These are') + ' already in Trash. ' +
                    ('Yahoo empties Trash on its own. ' if self.provider == 'yahoo' else '') + 'Nothing was changed.')
        self.pending = ('delete', ids, None)
        target = ('the marked message' if ids[0] in self.marked_ids else 'this message') if len(ids) == 1 else str(len(ids)) + ' marked messages'
        return 'I heard move ' + target + ' to Trash or Deleted Items. Is that right? Say yes or no.'

    def list_messages(self, cursor=None):
        names=('rows','next_cursor','current','view','body_text','body_offset','page_cursor','selected')
        saved={name:getattr(self,name) for name in names if hasattr(self,name)}
        try:return self._list_messages(cursor)
        except (AccountError,MailboxError,OSError):
            for name,value in saved.items():setattr(self,name,value)
            raise

    def _list_messages(self, cursor=None):
        self.rows = []
        self.next_cursor = None
        self.current = None
        self.view = 'folder'
        self.body_text = ''
        self.body_offset = 0
        if self.progress:
            self.progress('Getting messages from ' + self.folder_name + '. Please wait.')
        client = self.client()
        next_cursor = cursor
        while len(self.rows) < self.list_size:
            batch, next_cursor = client.list_messages(
                self.folder_id, next_cursor, limit=min(100, self.list_size - len(self.rows)))
            self.rows.extend(batch)
            if self.list_size != 1000: break
            if not batch:
                next_cursor = None
                break
            if not next_cursor: break
            if self.progress and self.list_size == 1000 and len(self.rows) % 200 == 0:
                self.progress('Retrieved ' + str(len(self.rows)) + ' messages. Still getting the rest.')
        self.next_cursor = next_cursor
        self.page_cursor = cursor
        self.selected = {index for index, row in enumerate(self.rows, 1)
                         if row['id'] in self.marked_ids}
        # Thunderbird: why the newest mail may be missing, said after the list (never silent).
        notice = getattr(client, 'notice', '') or ''
        notice = (' ' + notice) if isinstance(notice, str) and notice else ''
        if not self.rows:
            return 'Empty list. No messages on this page of ' + self.folder_name + '.' + notice
        self.current = 1
        return self.folder_name + '. ' + self._summary(1,True) + (' More messages are available.' if self.next_cursor else '') + notice

    def _summary(self, number, announce_boundary=False):
        row = self.rows[number - 1]
        import json
        try:preferences=json.loads((Path(getattr(self,'folder',''))/'preferences.json').read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError):preferences={}
        order=preferences.get('email_header_order','from, subject, date').split(',')
        parts=[]
        status = status_words(row) if preferences.get('email_status_words', True) else ''
        if status:
            parts.append(status)
        for header in order:
            header=header.strip().lower()
            if header not in ('from','subject','date','size'):continue
            value=sender_name(row) if header=='from' else row.get(header,'unavailable')
            if header=='size':
                value=(str(value)+' bytes') if isinstance(value,(int,float)) else str(value)
            if header=='subject':
                # Said as Alt+4 says it: "Lunch." not "Lunch..", and never a blank.
                value=' '.join(str(value or '').split()).rstrip('.').strip()
                if value in ('','(no subject)'):
                    parts.append('No subject');continue
            parts.append((header.capitalize()+' ' if preferences.get('email_header_names',False) else '')+str(value))
        boundary=self._message_boundary(number) if announce_boundary else ''
        return '. '.join(parts)+'. '+str(number)+' of '+str(len(self.rows))+'.'

    def message_status(self):
        """Read or unread, and flagged, replied, forwarded and importance when they apply."""
        row = self.rows[self.current - 1]
        return status_words(row, full=True) + '.'

    def change_status(self, name, value):
        """Mark the selected messages, or the one you are on: read or unread, flagged or not.
        value None turns it over: on if any of them is off, else off."""
        ids = self._action_ids()
        rows = [row for row in self.rows if row['id'] in ids]
        marker = getattr(self.client(), 'mark', None)
        if marker is None:
            return MARK_NEEDS_THUNDERBIRD
        now = {'read': lambda row: not row.get('unread'), 'flagged': lambda row: bool(row.get('flagged'))}[name]
        if value is None:
            value = not all(now(row) for row in rows) if rows else True
        result = marker(ids, **{name: value})
        for number, row in enumerate(self.rows, 1):
            if row['id'] in ids:
                if name == 'read':
                    row['unread'] = not value
                else:
                    row['flagged'] = value
                if row['id'] in self.marked_ids:
                    self.marked_ids[row['id']] = self._summary(number)
        words = {('read', True): 'marked as read', ('read', False): 'marked as unread',
                 ('flagged', True): 'flagged', ('flagged', False): 'not flagged now'}[(name, value)]
        done = result.get('changed', len(ids))
        if len(ids) == 1:
            said = {('read', True): 'Marked as read.', ('read', False): 'Marked as unread.',
                    ('flagged', True): 'Flagged.', ('flagged', False): 'Flag removed.'}[(name, value)]
        else:
            said = str(done) + ' ' + ('message' if done == 1 else 'messages') + ' ' + words + '.'
        missing = result.get('missing', 0)
        if missing:
            said += ' ' + str(missing) + (' message was' if missing == 1 else ' messages were') + \
                    ' not changed. It may have been moved or deleted. Say list messages to refresh.'
        return said

    def _message_boundary(self,number):
        first=number==1 and self.page_cursor is None
        last=number==len(self.rows) and not self.next_cursor
        return 'Start and end of list.' if first and last else 'Start of list.' if first else 'End of list.' if last else ''


    def _page_hint(self, older):
        """At the edge of a page: how to reach the next one, when there is one."""
        if older and self.next_cursor:
            return ' More messages are on the next page. Press Page Down, or say next page.'
        if not older and self.previous_cursors:
            return ' Newer messages are on the previous page. Press Page Up, or say previous page.'
        return ''

    def page_edge(self, bottom=False):
        """Home and End: the first or last message on this page. Already there: say so."""
        if not self.rows:return self._empty_list()
        target = len(self.rows) if bottom else 1
        if self.current == target:
            return ('End of list. ' if bottom else 'Start of list. ') + self._focus(target) + self._page_hint(bottom)
        return self._focus(target)

    # Going to the end one page at a time stops after this many pages, and says so.
    MAX_END_PAGES = 100

    def list_boundary(self,bottom=False):
        names=('rows','next_cursor','current','view','body_text','body_offset','page_cursor','previous_cursors','selected')
        saved={name:getattr(self,name) for name in names if hasattr(self,name)}
        if 'previous_cursors' in saved:saved['previous_cursors']=list(saved['previous_cursors'])
        try:return self._list_boundary(bottom)
        except (AccountError,MailboxError,OSError) as exc:
            for name,value in saved.items():setattr(self,name,value)
            return 'Could not reach the '+('end' if bottom else 'start')+' of the list. '+str(exc)

    def _list_boundary(self,bottom=False):
        if not self.rows:return self._empty_list()
        if not bottom and self.page_cursor is None and self.current==1:return 'Start of list.'
        if bottom and not self.next_cursor and self.current==len(self.rows):return 'End of list.'
        if not bottom:
            if self.page_cursor is not None:self.list_messages()
            self.previous_cursors=[]
            return self._focus(1)
        if not self.next_cursor:return self._focus(len(self.rows))   # the whole list is on this page
        client=self.client() if getattr(self,'provider','')=='thunderbird' and self.list_size in (10,20,30,40,50,100) else None
        if client is not None and hasattr(client,'last_page'):
            # One request for the last page (Thunderbird), never every page in turn:
            # a large mailbox took so long that Companion stopped answering.
            if self.progress:self.progress('Going to the end of '+self.folder_name+'. Please wait.')
            rows,next_cursor,offset=client.last_page(self.folder_id,self.list_size)
            self.rows=rows;self.next_cursor=next_cursor
            self.page_cursor=str(offset) if offset else None
            self.previous_cursors=[None]+[str(start) for start in range(self.list_size,offset,self.list_size)] if offset else []
            self.selected={index for index,row in enumerate(self.rows,1) if row['id'] in self.marked_ids}
            if not self.rows:
                self.current=None;self.view='folder';self.body_text='';self.body_offset=0
                return 'Empty list. No messages in '+self.folder_name+'.'
            return self._focus(len(self.rows))
        seen=set();pages=0;progress=self.progress
        if progress:progress('Going to the end of '+self.folder_name+'. Please wait.')
        try:
            # One "please wait", not one for every page.
            self.progress=None
            while self.next_cursor:
                pages+=1
                if pages>self.MAX_END_PAGES:
                    return ('This folder is too long to go to the end here. You are on page '+str(pages)+
                            '. Press Page Down for more, or press Home for the newest messages.')
                cursor=self.next_cursor;identity=repr(cursor)
                if identity in seen:return 'The server repeated a page. I could not reach the end of the list.'
                seen.add(identity);self.previous_cursors.append(self.page_cursor)
                self.list_messages(cursor)
                if not self.rows:return 'No messages returned on the last page.'
        finally:self.progress=progress
        return self._focus(len(self.rows))

    def _focus(self, number):
        self.current = self._number(number)
        self.view = 'folder'
        self.body_text = ''
        self.body_offset = 0
        return self._summary(self.current,True) + ' Say open current message to read it.'

    def _number(self, value):
        n = NUMBER_WORDS.get(str(value), None)
        if n is None:
            n = int(value)
        if n < 1 or n > len(self.rows):
            raise MailboxError('That number is not on this page. Say list messages.')
        return n

    def _read(self, n):
        number = self._number(n)
        row = self.rows[number - 1]
        body = self.client().read_message(row['id'], self.folder_id)
        if getattr(body, 'marked_read', False):
            row['unread'] = False   # Thunderbird marked it read, as when it is opened there
            if row['id'] in self.marked_ids:
                self.marked_ids[row['id']] = self._summary(number)
        self.current = number
        self.view = 'message'
        found = getattr(body, 'attachments', None)
        self.attachments = found if isinstance(found, list) else None
        self.attachments_for = (row['id'], self.folder_id)
        self.body_content=body or ''
        self.body_format=None
        import json
        try:format=json.loads((Path(self.folder)/'preferences.json').read_text(encoding='utf-8')).get('email_format','html')
        except (OSError,ValueError):format='html'
        self.set_format(format)
        self.body_offset = 0
        self.reading.set_text(self.body_text, reset=True)
        # Only "Message opened." before the words (Will, October 2026). From, To, CC, Subject
        # and Date have their own keys and questions (header()); the files are still named first.
        return 'Message opened. ' + self._attachment_note() + self._next_body()

    def header(self, name):
        """One line of the message's header, said on its own: From, To, CC, Subject, Date sent, or the files."""
        if not self.rows or not self.current:
            return 'Open a message first.' if self.rows else 'Empty list. No messages on this page.'
        row = self.rows[self.current - 1]
        if name == 'from':
            address = sender_address(row)
            who = sender_name(row)
            if who in ('Unknown sender', 'Sender name unavailable') and address:
                return 'From: ' + address + '.'
            return 'From: ' + who + (', ' + address if address and address.casefold() != who.casefold() else '') + '.'
        if name == 'subject':
            subject = ' '.join(str(row.get('subject') or '').split())
            return 'No subject.' if subject in ('', '(no subject)') else 'Subject: ' + subject.rstrip('.') + '.'
        if name == 'date':
            when = row.get('date')
            return ('Sent ' + when + '.') if when and when != 'unavailable' else 'The date it was sent is not known.'
        if name == 'attachments':
            from mail_attachments import count_words
            same = self.view == 'message' and getattr(self, 'attachments_for', None) == (row['id'], self.folder_id)
            found = self.attachments if same else self.client().attachments(row['id'], self.folder_id)
            if found is None:
                return 'I could not check this message for attachments.'
            return count_words(len(found)) + '.' if found else 'No attachments.'
        key = (row['id'], self.folder_id)
        cache = self.__dict__.setdefault('_recipients', {})
        if key not in cache:
            reader = getattr(self.client(), 'message_headers', None)
            if reader is None:
                return 'This email account cannot read the To and CC lines.'
            cache.clear()
            cache[key] = reader(row['id'], self.folder_id)
        people = cache[key].get(name) or []
        if not people:
            return 'No CC.' if name == 'cc' else 'No To address.'
        return ('To: ' if name == 'to' else 'CC: ') + spoken_people(people) + '.'

    def _attachment_note(self):
        """Said before the message: its files, with names and sizes."""
        if not self.attachments:
            return ''
        from mail_attachments import summary
        return summary(self.attachments) + (' Press L to list it. ' if len(self.attachments) == 1 else ' Press L to list them. ')

    def set_format(self,format):
        if getattr(self,'body_format',None)==format or not hasattr(self,'body_content'):return
        self.body_format=format
        self.message_control=None
        self.body_text=self.body_content if format=='html' else getattr(self.body_content,'plain',str(self.body_content))
        self.reading.set_text(self.body_text,reset=True)

    def _next_body(self):
        if not self.body_text:
            return 'No readable body.'
        result = self.reading.read().replace('End of text.', 'End of message.')
        result=self._spoken_read_result(result)
        self.body_offset = self.reading.continuation
        return result

    def _spoken_read_result(self,result):
        from email_reader import spoken_range
        plain=self.body_text[self.reading.position:self.reading.continuation].strip()
        if plain and result.startswith(plain):result=spoken_range(self.body_text,self.reading.position,self.reading.continuation)+result[len(plain):]
        return result

    def message_element(self,role='link',direction=1,activate=False):
        from email_reader import message_controls,safe_link
        if self.view!='message':return 'Open a message first.'
        if getattr(self,'body_format',None)!='html':return 'Choose HTML under Options, Email, Email format to navigate links and buttons.'
        controls=message_controls(self.body_text,role)
        if activate:
            item=next((span for span in message_controls(self.body_text) if span[0]<=self.reading.position<span[1]),None)
            if not item:return 'Move to a link or button first.'
            if not safe_link(item[3]):return 'This email button has no safe link to open.'
            # Companion opens it in the guided browser chosen in Web browser options.
            self.link_request=item[3];return 'Opening '+' '.join(self.body_text[item[0]:item[1]].split())+'.'
        current=getattr(self,'message_control',None)
        candidates=[span for span in controls if span[0]<self.reading.position] if direction<0 else [span for span in controls if span[0]>self.reading.position or span[0]==self.reading.position==0 and current!=span]
        if not candidates:return 'No '+('previous ' if direction<0 else 'next ')+role+'.'
        item=candidates[-1] if direction<0 else candidates[0];self.message_control=item
        self.reading.position=self.reading.continuation=item[0]
        # Said as the web browser says it: "Link Name." or "Name, button." (Will, October 2026).
        name=' '.join(self.body_text[item[0]:item[1]].split()) or 'Unlabeled'
        return ('Link '+name+'.') if item[2]=='link' else name+', button.'

    def response_context(self, action):
        if self.current is None or self.view != 'message':
            said = {'reply': 'reply', 'reply_all': 'reply all', 'forward': 'forward'}.get(action, 'it again')
            raise MailboxError('Open the message first. Press Enter, or say open message. Then say ' + said + '.')
        address, _ = account_token(self.provider, self.folder)
        row = self.rows[self.current - 1]
        context = self.client().response_context(row['id'], action, address, self.folder_id)
        context['source_address'] = address
        self._clear_marks()
        self.pending = None
        return context

    def process(self, text):
        spoken = text.strip().rstrip('.!?')
        # Recognizers add inner punctuation ("Yes, please."), as companion.handle allows for.
        command = re.sub(r'\s+', ' ', re.sub(r'\s*[,;!?]+\s*', ' ', spoken.lower())).strip()
        command = EVERYDAY_COMMANDS.get(command, command)
        if INBOX_WORDS.fullmatch(command):
            command = 'open inbox'
        size = re.fullmatch(r'(?:set |change |show )?(?:email |mail |message )?(?:list size|page size|messages per page|emails per page)(?: to)? (all|\d+|one thousand)', command)
        size = size or re.fullmatch(r'(?:set|change) (?:list )?to (all|\d+|one thousand)', command)
        size = size or re.fullmatch(r'(?:list |list of |list to |show |show me )(all|\d+|one thousand)(?: (?:number of )?(?:messages|emails))?', command)
        size = size or re.fullmatch(r'(all|\d+|one thousand) (?:number of )?(?:messages|emails)(?: per page| at a time)?', command)
        if size:
            value = '1000' if size[1] == 'one thousand' else size[1]
            if value != 'all' and (not value.isdigit() or int(value) not in MAIL_SIZES):
                return 'Choose 10, 20, 30, 40, 50, 100, 1000, or all messages.'
            self.all_messages = value == 'all'
            self.list_size = 10 if self.all_messages else int(value)
            try:
                Path(self.folder).mkdir(parents=True, exist_ok=True)
                write_text_atomic(Path(self.folder) / 'email-list-size.txt', value)
            except OSError:
                return 'I could not save that email list setting. Try again.'
            self.previous_cursors.clear()
            result = self.list_messages()
            return (ALL_MESSAGES + ' ' if self.all_messages else
                    'Showing ' + value + ' messages at a time. ') + result
        if command in ('email list size', 'mail list size', 'how many emails', 'how many messages per page'):
            return (ALL_MESSAGES if self.all_messages else
                    str(self.list_size) + ' messages at a time.') + ' Say set to 20, set list to 1000, or set to all.'
        open_selection = ('that one', 'okay', 'ok', 'confirm', 'confirm that')
        if self.pending and command in open_selection and command not in ('okay', 'ok'):
            return 'An action is waiting for confirmation. Say yes or no first.'
        if self.pending and command in ('no', 'no thanks', 'cancel'):
            self.pending = None
            return 'No. Canceled. Nothing was changed.'
        if self.pending and command in ('yes', 'yes please', 'okay', 'ok'):
            command = {'create_folder':'confirm create folder', 'rename_folder':'confirm rename folder',
                       'move_folder':'confirm move folder', 'delete_folder':'confirm delete folder',
                       'move':'confirm move', 'delete':'confirm delete'}[self.pending[0]]
        if command not in ('confirm move', 'confirm delete', 'confirm create folder',
                           'confirm rename folder', 'confirm move folder', 'confirm delete folder'):
            self.pending = None
        try:
            if self.folder_picker:
                if command in ('cancel', 'go back', 'back'):
                    self.folder_picker = None
                    self.folder_choice = None
                    return 'Folder choice canceled. Nothing was changed.'
                response = self._folder_pick(command)
                if response is not None:
                    return response
                if command in ('list folders', 'what folders', 'my folders'):
                    index=self.folder_picker['index']
                    action = self.folder_picker['action']
                    verb = {'delete':'delete it', 'message_move':'move the messages there',
                            'move_current':'move this message there'}.get(action, 'choose it')
                    return (name_first('Folder '+str(index+1)+' of '+str(len(self.folder_picker['folders']))+': '+
                            self._spoken_folder(self.folder_picker['folders'][index][0])+'. Say next or previous, then that one to '+verb+'.'))
                self.folder_picker = None
                self.folder_choice = None
            if self.folder_choice:
                action, name, ident = self.folder_choice
                self.folder_choice = None
                if action=='create':
                    if command in ('cancel','go back','back'):return 'Folder creation canceled.'
                    return self.process(spoken if command.startswith('create folder ') else 'create folder '+spoken)
                match = re.fullmatch(r'rename to (.+)', spoken, flags=re.I)
                if action == 'rename' and match:
                    self.folders = self.client().folders()
                    if (name, ident) not in self.folders:
                        return 'The folder changed. Say rename folder and start again.'
                    return self.process('rename folder ' + name + ' to ' + match[1])
                if command in ('cancel', 'go back', 'back'):
                    return 'Folder choice canceled. Nothing was changed.'
                return 'Say rename to followed by the new name, or cancel.'
            if (not self.rows and self.view == 'folder' and (command in EMPTY_LIST_COMMANDS or header_request(command)) and
                    not (self.marked_ids and command in ('delete', 'move', 'move to folder'))):
                # An empty folder is an empty list, the same for every key and command.
                return self._empty_list()
            if command in ('first message on page','last message on page') and self.rows and not self.folder_picker:
                return self.page_edge(command.startswith('last'))
            if command in ('newest message','first message in folder','oldest message','last message in folder') and self.rows and not self.folder_picker:
                return self.list_boundary(command in ('oldest message','last message in folder'))
            if command in ('first message','start of list','beginning of list','last message','end of list') and self.rows and not self.folder_picker:
                return self.page_edge(command in ('last message','end of list'))
            if command in ('next','previous') and self.rows:
                command += ' message'
            if (command in STATUS_CHANGES or command in STATUS_QUESTIONS) and not self.rows:
                return self._empty_list()
            if command in STATUS_QUESTIONS and self.rows and self.current:
                return self.message_status()
            if command in STATUS_CHANGES and self.rows:
                return self.change_status(*STATUS_CHANGES[command])
            if command in ('message details','who is this from','read message details') and (self.view == 'message' or self.rows and self.current):
                # In the list this repeats the summary of the message you are on.
                from mail_attachments import summary
                files = ' ' + summary(self.attachments) if self.view == 'message' and self.attachments else ''
                return self._summary(self.current) + files
            asked = header_request(command)
            if asked:
                return self.header(asked)
            if self.view=='message' and command in ('next link','previous link','next button','previous button'):
                direction,role=command.split();return self.message_element(role,-1 if direction=='previous' else 1)
            if self.view=='message' and command in ('activate current element','activate current link','activate current button','open current link','open current button'):
                return self.message_element(activate=True)
            request = reading_request(command)
            if self.view == 'message' and request:
                unit, direction = request
                result = (self.reading.read(direction == 'top').replace('End of text.', 'End of message.')
                          if unit == 'read' else self.reading.move(unit, direction))
                if unit=='read':result=self._spoken_read_result(result)
                if unit in ('line','sentence','paragraph'):
                    from email_reader import spoken_range
                    span=next((s for s in self.reading._spans(unit) if s.start()==self.reading.position),None)
                    if span:result=spoken_range(self.body_text,span.start(),span.end())
                self.body_offset = self.reading.continuation
                return result
            if command in ('back to folder', 'return to folder', 'message list'):
                self.view = 'folder'
                return self._current_list()
            if command in ('which folder', 'where am i'):
                return 'You are in ' + self.folder_name + '. ' + ('A message is open.' if self.view == 'message' else 'The message list is open.')
            if command in ('list folders', 'what folders', 'my folders'):
                return self.list_folders()
            if command in ('go to folder', 'open folder'):
                return self._choose_folder('open')
            match = re.fullmatch(r'(?:open|go to) folder (.+)', command)
            if match:
                return self._open_named_folder(match[1])
            if command in ('open inbox', 'go to inbox'):
                self.folder_name = 'Inbox'
                self.folder_id = 'inbox'
                self.previous_cursors.clear()
                self._clear_marks()
                return self.list_messages()
            if command in ('back to inbox', 'return to inbox'):
                self.folder_name = 'Inbox'
                self.folder_id = 'inbox'
                self.previous_cursors.clear()
                self._clear_marks()
                return self.list_messages()
            # "Go to message 2", "go to the next message": a message on this page, not a folder.
            match = re.fullmatch(r'go to (?:the )?(next|previous|first|last|newest|oldest) (?:message|email)', command)
            if match:
                return self.process(match[1] + ' message')
            match = re.fullmatch(r'go to (?:message|email) (' + NUMBER + r')', command)
            if match:
                return self._focus(match[1]) if self.rows else self._empty_list()
            if command.startswith('go to ') and command not in ('go to main menu',):
                return self._open_named_folder(command[len('go to '):])
            if command in ('list messages', 'read message list', 'refresh messages'):
                self.previous_cursors.clear()
                return self.list_messages()
            if command in ('next messages', 'next page', 'next list', 'get more emails',
                           'more emails', 'show more emails', 'show more messages', 'get more messages'):
                if not self.next_cursor:
                    return 'End of list. There are no more messages on this list.'
                previous=self.page_cursor
                result=self.list_messages(self.next_cursor)
                self.previous_cursors.append(previous)
                return result
            if command in ('previous messages', 'previous page', 'previous list',
                           'go back a list', 'back a list', 'back one list', 'earlier emails'):
                if not self.previous_cursors:
                    return 'Start of list. This is the first page of messages.'
                result=self.list_messages(self.previous_cursors[-1])
                self.previous_cursors.pop()
                return result
            if command in open_selection and self.view == 'folder':
                return self._read(self.current) if self.current is not None else 'There is no highlighted message. Say list messages.'
            match = re.fullmatch(r'(?:read|open) message (' + NUMBER + r')', command)
            if match:
                return self._read(match[1])
            # "Open the first message", "read the last email".
            match = re.fullmatch(r'(?:read|open) (?:the )?(' + NUMBER + r'|last) (?:message|email)', command)
            if match:
                if not self.rows:
                    return self._empty_list()
                return self._read(len(self.rows) if match[1] == 'last' else match[1])
            # "Read the next message": the next one, opened. At the start or end of the page, said as Up and Down say it.
            match = re.fullmatch(r'(?:read|open) (?:the )?(next|previous) (?:message|email|one)', command)
            if match:
                target = (self.current or 0) + (1 if match[1] == 'next' else -1)
                if not self.rows or self.current is None or not 1 <= target <= len(self.rows):
                    return self.process(match[1] + ' message')
                return self._read(target)
            if command in ('read current message', 'open current message', 'repeat message',
                           'read message', 'open message', 'open that', 'read that',
                           'open it', 'read email', 'open email'):
                return self._read(self.current) if self.current else 'Say read message followed by a number first.'
            if command in ('list links','read links','list buttons','read buttons') or re.fullmatch(r'(?:open|activate) (?:link|button) (\d+)',command):
                import json
                try:format=json.loads((Path(self.folder)/'preferences.json').read_text(encoding='utf-8')).get('email_format','html')
                except (OSError,ValueError):format='html'
                if self.view!='message':return 'Open a message first.'
                if format!='html':return 'Choose HTML under Options, Email, Email format to navigate links.'
                from email_reader import message_controls,safe_link
                role='button' if 'button' in command else 'link'
                links=message_controls(self.body_text,role)
                if command.startswith(('list ','read ')):
                    return '. '.join(str(i+1)+'. '+self.body_text[a:b]+', '+style for i,(a,b,style,url) in enumerate(links)) or 'No '+role+'s in this message.'
                number=int(command.split()[-1])
                if not 1<=number<=len(links):return 'That link number is not in this message.'
                if not safe_link(links[number-1][3]):return 'This email button has no safe link to open.'
                self.link_request=links[number-1][3];return 'Opening '+role+' '+str(number)+'.'
            if command in ('continue reading', 'read more', 'continue message'):
                return self._next_body() if self.view == 'message' else 'Open a message first.'
            if command in ('read from beginning', 'start message over'):
                if self.view != 'message': return 'Open a message first.'
                self.reading.set_text(self.body_text, reset=True)
                return self._next_body()
            if command in ('next message', 'previous message'):
                if self.current is None:
                    return self._focus(1) if command == 'next message' and self.rows else 'Say read message followed by a number first.'
                delta = 1 if command == 'next message' else -1
                # A page is a list with a hard start and end, like every menu: the arrows
                # stop there. Another page is opened only on purpose (Page Down, Page Up).
                if delta == 1 and self.current == len(self.rows):
                    return 'End of list. ' + self._focus(self.current) + self._page_hint(True)
                if delta == -1 and self.current == 1:
                    return 'Start of list. ' + self._focus(self.current) + self._page_hint(False)
                return self._focus(self.current + delta)
            if command in ('extend selection next','extend selection previous'):
                if not self.rows:return 'No messages to select.'
                self._mark(self.current)
                response=self.process('next message' if command.endswith('next') else 'previous message')
                self._mark(self.current)
                return response+' '+str(len(self.marked_ids))+' selected.'
            if command=='toggle message selection':
                if not self.rows:return 'No messages to select.'
                if self.current in self.selected:
                    self._unmark(self.current)
                    return 'Unmarked message '+str(self.current)+'. '+self._selected_count()
                self._mark(self.current)
                return 'Marked message '+str(self.current)+'. '+self._selected_count()
            if command=='select all messages':
                for number in range(1,len(self.rows)+1):self._mark(number)
                return plural(len(self.rows), 'message')+' on this page selected.'
            if command in ('select', 'mark', 'select message', 'mark message', 'select this message', 'mark this message'):
                self._current_ids()
                self._mark(self.current)
                return 'Marked message ' + str(self.current) + '. ' + self._selected_count()
            match = re.fullmatch(r'(?:select|mark) message (' + NUMBER + r')', command)
            if match:
                n = self._number(match[1])
                self._mark(n)
                return 'Marked message ' + str(n) + '. ' + self._selected_count()
            if command in ('unmark message', 'deselect message', 'unselect message'):
                self._current_ids()
                if self.current not in self.selected:
                    return 'Message ' + str(self.current) + ' is not marked. ' + self._selected_count()
                self._unmark(self.current)
                return 'Unmarked message ' + str(self.current) + '. ' + self._selected_count()
            match = re.fullmatch(r'select messages (' + NUMBER + r') (?:through|to) (' + NUMBER + r')', command)
            if match:
                first, last = self._number(match[1]), self._number(match[2])
                if first > last:
                    return 'Say the first number before the last number.'
                for n in range(first, last + 1): self._mark(n)
                return 'Marked messages ' + str(first) + ' through ' + str(last) + '. ' + self._selected_count()
            if command in ('clear selection', 'cancel selection'):
                self._clear_marks()
                return 'Selection cleared.'
            if command in ('what is selected', 'read selection'):
                return ('Marked ' + plural(len(self.marked_ids), 'message') + ' across this folder.'
                        if self.marked_ids else 'No messages selected.')
            if command in ('move selected', 'move selected to folder', 'move selected to'):
                return self._choose_folder('message_move', self._selected_ids())
            if command in ('move', 'move to folder'):
                ids = self._action_ids()
                return self._choose_folder('message_move' if self.marked_ids else 'move_current', ids)
            if command in ('move message', 'move this message', 'move message to folder'):
                return self._choose_folder('move_current', self._current_ids())
            if command == 'delete folder': return self._choose_folder('delete')
            if command == 'create folder':
                self.folder_choice=('create','','')
                return 'New folder name. Say or type create folder followed by its name, then press Enter.'
            if command == 'rename folder': return self._choose_folder('rename')
            if command == 'move folder': return self._choose_folder('move_source')
            match = re.fullmatch(r'create folder (.+?) inside', spoken, flags=re.I)
            if match:
                return self._choose_folder('create_parent', self._folder_name(match[1]))
            match = re.fullmatch(r'create folder (.+?) inside (.+)', spoken, flags=re.I)
            if match:
                name = self._folder_name(match[1])
                parent, parent_id = self._find_folder(match[2])
                self._parent_allowed(parent_id)
                path = parent + '/' + name
                if any(title.casefold() == path.casefold() for title, _ in self.folders):
                    return 'That folder already exists.'
                self.pending = ('create_folder', name, parent_id, parent + '/')
                return 'I heard create folder ' + path + '. Is that right? Say yes or no.'
            match = re.fullmatch(r'create folder (.+)', spoken, flags=re.I)
            if match:
                name = self._folder_name(match[1])
                if any(title.casefold() == name.casefold() for title, _ in self.folders):
                    return 'That folder already exists.'
                self.pending = ('create_folder', name, None, '')
                return 'I heard create top-level folder ' + name + '. Is that right? Say yes or no.'
            match = re.fullmatch(r'rename folder (.+?) to (.+)', spoken, flags=re.I)
            if match:
                old, ident = self._find_folder(match[1])
                self._mutable_folder(old, ident)
                name = self._folder_name(match[2])
                parent = old.rsplit('/', 1)[0] + '/' if '/' in old else ''
                if any(title.casefold() == (parent + name).casefold() for title, _ in self.folders):
                    return 'That folder name already exists.'
                self.pending = ('rename_folder', ident, old, name, parent)
                return 'I heard rename folder ' + old + ' to ' + parent + name + '. Is that right? Say yes or no.'
            match = re.fullmatch(r'move folder (.+?) into (.+)', command)
            if match:
                old, ident = self._find_folder(match[1])
                self._mutable_folder(old, ident)
                destination, destination_id = self._find_folder(match[2])
                self._parent_allowed(destination_id)
                if destination == old or destination.startswith(old + '/'):
                    return 'A folder cannot be moved inside itself or one of its children.'
                path = destination + '/' + old.rsplit('/', 1)[-1]
                if any(title.casefold() == path.casefold() for title, _ in self.folders):
                    return 'A folder with that name already exists in the destination.'
                self.pending = ('move_folder', ident, old, destination_id, destination + '/')
                return 'I heard move folder ' + old + ' into ' + destination + '. Is that right? Say yes or no.'
            match = re.fullmatch(r'delete folder (.+)', command)
            if match:
                self.folders = self.client().folders()
                title, ident = self._find_folder(match[1])
                self._mutable_folder(title, ident)
                info = self.client().folder_info(ident)
                if info.get('messagesTotal', info.get('totalItemCount', 0)) or info.get('childFolderCount', 0):
                    return 'That folder is not empty. Move its messages and child folders first.'
                self.pending = ('delete_folder', ident, title)
                return 'I heard delete empty folder ' + title + '. Is that right? Say yes or no.'
            folder_commands = {'confirm create folder': 'create_folder',
                               'confirm rename folder': 'rename_folder',
                               'confirm move folder': 'move_folder',
                               'confirm delete folder': 'delete_folder'}
            if command in folder_commands:
                if not self.pending or self.pending[0] != folder_commands[command]:
                    return 'Nothing is awaiting that folder confirmation.'
                action, *args = self.pending
                self.pending = None
                client = self.client()
                self.folders = client.folders()
                def still_exists(path, ident):
                    return any(title == path and folder_id == ident for title, folder_id in self.folders)
                if action == 'create_folder':
                    name, parent_id, parent_path = args
                    if parent_id and not still_exists(parent_path.rstrip('/'), parent_id):
                        return 'The parent folder changed. Say list folders and start again.'
                    if any(title.casefold() == (parent_path + name).casefold() for title, _ in self.folders):
                        return 'That folder now exists. Creation canceled.'
                    client.create_folder(*args)
                elif action == 'rename_folder':
                    ident, old, name, parent = args
                    if not still_exists(old, ident) or any(title.casefold() == (parent + name).casefold() for title, _ in self.folders):
                        return 'The folder list changed. Rename canceled; say list folders.'
                    self._mutable_folder(old, ident)
                    client.rename_folder(ident, name, parent)
                elif action == 'move_folder':
                    ident, old, destination_id, destination_path = args
                    target = destination_path + old.rsplit('/', 1)[-1]
                    if (not still_exists(old, ident) or
                            not still_exists(destination_path.rstrip('/'), destination_id) or
                            any(title.casefold() == target.casefold() for title, _ in self.folders)):
                        return 'The folder list changed. Move canceled; say list folders.'
                    self._mutable_folder(old, ident)
                    client.move_folder(ident, old, destination_id, destination_path)
                else:
                    ident, title = args
                    if not still_exists(title, ident):
                        return 'The folder changed. Deletion canceled; say list folders.'
                    self._mutable_folder(title, ident)
                    info = client.folder_info(ident)
                    if info.get('messagesTotal', info.get('totalItemCount', 0)) or info.get('childFolderCount', 0):
                        return 'Folder deletion canceled because it is not empty.'
                    client.delete_folder(ident)
                self.folders = []
                self.folder_name, self.folder_id = 'Inbox', 'inbox'
                self.previous_cursors.clear()
                self._clear_marks()
                done = 'Folder change completed. Say list folders to check it. '
                try:
                    # The Inbox itself, so the next key is on a message, never "Empty list".
                    return done + self.list_messages()
                except (MailboxError, AccountError, OSError):
                    self._uncertain_change()
                    return done + 'The Inbox could not load. Say list messages to try again.'
            if command.startswith('move selected to '):
                ids = self._selected_ids()
                name = command[len('move selected to '):].strip()
                if name == 'folder': return self._choose_folder('message_move', ids)
                if not self.folders:
                    self.folders = self.client().folders()
                matches = [(title, ident) for title, ident in self.folders if title.casefold() == name.casefold()]
                if not matches:
                    matches = [(title, ident) for title, ident in self.folders if title.rsplit('/',1)[-1].casefold() == name.casefold()]
                if len(matches) > 1: return self._choose_folder('message_move', ids, matches, name)
                if not matches: return 'I could not find that destination folder. Say move selected to folder.'
                title, ident = matches[0]
                return self._prepare_move(ids, title, ident)
            match = re.fullmatch(r'move (?:this )?message to (.+)|move to (.+)', command)
            if match:
                contextual = match[2] is not None
                ids = self._action_ids() if contextual else self._current_ids()
                pick_action = 'message_move' if contextual and self.marked_ids else 'move_current'
                name = next(group for group in match.groups() if group is not None).strip()
                if name == 'folder': return self._choose_folder(pick_action, ids)
                if not self.folders: self.folders = self.client().folders()
                matches = [(title, ident) for title, ident in self.folders if title.casefold() == name.casefold()]
                if not matches:
                    matches = [(title, ident) for title, ident in self.folders if title.rsplit('/',1)[-1].casefold() == name.casefold()]
                if len(matches) > 1: return self._choose_folder(pick_action, ids, matches, name)
                if not matches: return 'I could not find that destination folder. Say move message to folder.'
                return self._prepare_move(ids, *matches[0])
            if command == 'delete selected':
                return self._prepare_delete(self._selected_ids())
            if command == 'delete':
                return self._prepare_delete(self._action_ids())
            if command in ('delete message', 'delete this message'):
                return self._prepare_delete(self._current_ids())
            if command in ('confirm move', 'confirm delete'):
                action = 'move' if command == 'confirm move' else 'delete'
                if not self.pending or self.pending[0] != action:
                    return 'Nothing is awaiting that confirmation.'
                _, ids, destination = self.pending
                self.pending = None
                # The message you were on: afterwards you are on the one that took its place.
                place = (self.current if self.current and 1 <= self.current <= len(self.rows) and
                         ids == [self.rows[self.current - 1]['id']] else None)
                self._clear_marks()
                try:
                    if action == 'move':
                        self.client().move(ids, self.folder_id, destination)
                    else:
                        if self.provider == 'yahoo':
                            self.client().trash(ids, self.folder_id)
                        else:
                            self.client().trash(ids)
                except (MailboxError, AccountError) as exc:
                    if isinstance(exc, AccountError) or exc.unchanged:
                        # The mail service refused before anything moved; say why.
                        return str(exc) + ('' if 'Nothing was' in str(exc) else ' Nothing was changed.')
                    return self._uncertain_change()
                except Exception:
                    return self._uncertain_change()
                count = str(len(ids)) + (' message ' if len(ids) == 1 else ' messages ')
                result = count + ('moved.' if action == 'move' else 'moved to Trash or Deleted Items.')
                try:
                    refreshed = self.list_messages(self.page_cursor)
                    if not self.rows and self.previous_cursors:
                        refreshed = self.list_messages(self.previous_cursors.pop())
                    elif place and place > 1 and self.rows:
                        first = self._summary(1)
                        self.current = min(place, len(self.rows))
                        refreshed = refreshed.replace(first, self._summary(self.current), 1)
                    return result + ' ' + refreshed
                except Exception:
                    self.rows = []
                    self.current = None
                    return result + ' The folder could not reload. Say list messages to try again.'
            if command in ('cancel', 'cancel move', 'cancel delete'):
                self.pending = None
                return 'Canceled. Nothing was changed.'
            return ('I did not understand that. Say go to folder to choose from a list, next folder, previous folder, okay, back to inbox, '
                    'list messages, read message 1, next message, previous message, '
                    'reply, reply all, forward, mark message, move, delete, '
                    'create folder, rename folder, move folder, or delete empty folder.')
        except MailboxError as exc:
            self.pending = None
            self.folders = []
            return str(exc)
        except AccountError as exc:
            self.pending = None
            return str(exc)
        except (ValueError, KeyError, IndexError):
            self.pending = None
            return 'I could not complete that mail request. Check the account connection and say list messages to try again.'
        except Exception as exc:
            self.pending = None
            self.folders = []
            # Record code locations and exception type only, never tokens or message content.
            try:
                frames = traceback.extract_tb(exc.__traceback__)
                detail = type(exc).__module__ + '.' + type(exc).__name__ + '\n'
                detail += '\n'.join(Path(f.filename).name + ':' + str(f.lineno) + ' in ' + f.name for f in frames)
                Path(self.folder).mkdir(parents=True, exist_ok=True)
                (Path(self.folder) / 'Email-Error.txt').write_text(detail, encoding='utf-8')
            except OSError:
                pass
            return 'Email could not complete that request. Ask your helper to check Email-Error.txt in the Voice Companion settings folder. If you were moving or deleting messages, check the mailbox before trying again.'

    def _uncertain_change(self):
        """The mail service stopped partway: no list or open message is kept that may be wrong."""
        self.rows, self.current, self.view = [], None, 'folder'
        self.body_text, self.body_offset = '', 0
        self.reading.set_text('', reset=True)
        return UNCERTAIN_CHANGE

    def _empty_list(self):
        return ('Empty list. No messages on this page of ' + self.folder_name +
                '. Press F5 or say list messages to check again.')

    def _current_list(self):
        if not self.rows:
            return self.list_messages()
        # The message you were on, as in any list; never the whole page read aloud.
        if self.current is None or not 1 <= self.current <= len(self.rows):
            self.current = 1
        return 'Back to ' + self.folder_name + '. ' + self._summary(self.current)


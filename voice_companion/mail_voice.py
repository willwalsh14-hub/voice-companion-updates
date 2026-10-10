"""Voice state for a single connected mailbox; no persistent mail cache."""
from list_announcements import name_first
from menu_navigation import menu_label
import re
import traceback
from pathlib import Path

from mail_accounts import AccountError, account_token
from mailbox_access import MailboxClient, MailboxError
from yahoo_mailbox import YahooMailbox
from reading_navigation import ReadingCursor, reading_request

NUMBER_WORDS = {'one': 1, 'first': 1, 'two': 2, 'second': 2,
                'three': 3, 'third': 3, 'four': 4, 'fourth': 4,
                'five': 5, 'fifth': 5, 'six': 6, 'sixth': 6,
                'seven': 7, 'seventh': 7, 'eight': 8, 'eighth': 8,
                'nine': 9, 'ninth': 9, 'ten': 10, 'tenth': 10}
NUMBER = r'(?:\d+|' + '|'.join(NUMBER_WORDS) + r')'
MAIL_SIZES = (10, 20, 30, 40, 50, 100, 1000)


def sender_address(row):
    from email.utils import getaddresses
    candidates=getaddresses([row.get('sender_address') or row.get('from','')])
    return next((address for _,address in candidates if '@' in address),'')

def sender_name(row):
    from email.utils import parseaddr
    name,address=parseaddr(row.get('from',''))
    return row.get('sender_name') or name or (row.get('from','') if '@' not in row.get('from','') else 'Sender name unavailable') or 'Unknown sender'

class MailSession:
    def __init__(self, provider, folder, client_factory=MailboxClient, progress=None):
        self.provider, self.folder = provider, folder
        self.client_factory = client_factory
        self.progress = progress
        self.folders = []
        self.folder_name = 'Inbox'
        self.folder_id = 'INBOX' if provider == 'gmail' else 'inbox'
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
        try:
            setting = (Path(folder) / 'email-list-size.txt').read_text(encoding='utf-8').strip()
            self.all_messages = setting == 'all'
            if setting.isdigit() and int(setting) in MAIL_SIZES:
                self.list_size = int(setting)
        except OSError:
            pass

    def client(self):
        address, token = account_token(self.provider, self.folder)
        if self.provider == 'yahoo':
            return YahooMailbox(address, token)
        return self.client_factory(self.provider, token)

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
        if title.casefold() in ('inbox', 'sent', 'sent items', 'drafts', 'trash',
                                'deleted items', 'junk', 'junk email', 'spam', 'all mail', 'archive'):
            raise MailboxError('That standard folder cannot be changed by voice.')
        if self.provider == 'gmail' and self.client().folder_info(ident).get('type', '').lower() != 'user':
            raise MailboxError('That Gmail system label cannot be changed.')
        if self.provider in ('gmail', 'yahoo') and any(path.startswith(title + '/') for path, _ in self.folders):
            raise MailboxError('This folder has child folders. Move the children first.')

    def _parent_allowed(self, ident):
        if self.provider == 'gmail' and self.client().folder_info(ident).get('type', '').lower() != 'user':
            raise MailboxError('A Gmail system label cannot contain a new label.')

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
        return ('Found '+str(len(folders))+' folders. Folder 1: '+menu_label(self._spoken_folder(folders[0][0]))+
                '. Say next or previous, then that one to ' + verb + '. Say cancel to leave this list.')

    def _open_named_folder(self, spoken):
        self.folders = self.client().folders()
        name = spoken.strip().replace(' slash ', '/').casefold()
        if '/' in name:
            matches = [item for item in self.folders if item[0].casefold() == name]
        else:
            matches = [item for item in self.folders if item[0].rsplit('/', 1)[-1].casefold() == name]
        if not matches and name in ('sent', 'sent mail', 'sent items'):
            matches = [item for item in self.folders if item[0].casefold() in ('sent', 'sent mail', 'sent items')
                       or item[1].casefold() in ('sent', 'sentitems')]
        if not matches:
            standard = {'drafts': ('draft', 'drafts'), 'draft': ('draft', 'drafts'),
                        'trash': ('trash', 'deleteditems'), 'spam': ('spam', 'junkemail'),
                        'sent': ('sent', 'sentitems')}.get(name, ())
            matches = [item for item in self.folders if item[1].casefold() in standard]
        if not matches:
            return 'I could not find that folder. Say go to folder to hear the list.'
        if len(matches) > 1:
            return self._choose_folder('open', candidates=matches, ambiguous_name=spoken)
        self.folder_name, self.folder_id = matches[0]
        self.previous_cursors.clear()
        self._clear_marks()
        return self.list_messages()

    def _clear_marks(self):
        self.selected.clear()
        self.marked_ids.clear()

    def _mark(self, number):
        row = self.rows[number - 1]
        self.selected.add(number)
        self.marked_ids[row['id']] = self._summary(number)

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
                return 'That is the ' + ('last' if step > 0 else 'first') + ' folder. ' + picker['folders'][picker['index']][0] + '.'
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
        self.pending = ('move', ids, ident)
        target = ('the marked message' if ids[0] in self.marked_ids else 'this message') if len(ids) == 1 else str(len(ids)) + ' marked messages'
        return 'I heard move ' + target + ' to ' + self._spoken_folder(title) + '. Is that right? Say yes or no.'

    def _prepare_delete(self, ids):
        self.pending = ('delete', ids, None)
        target = ('the marked message' if ids[0] in self.marked_ids else 'this message') if len(ids) == 1 else str(len(ids)) + ' marked messages'
        return 'I heard move ' + target + ' to Trash or Deleted Items. Is that right? Say yes or no.'

    def list_messages(self, cursor=None):
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
        if not self.rows:
            return 'No messages on this page of ' + self.folder_name + '.'
        self.current = 1
        return self.folder_name + '. ' + self._summary(1) + (' More messages are available.' if self.next_cursor else '')

    def _summary(self, number):
        row = self.rows[number - 1]
        import json
        try:preferences=json.loads((Path(getattr(self,'folder',''))/'preferences.json').read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError):preferences={}
        order=preferences.get('email_header_order','from, subject, date').split(',')
        parts=[]
        for header in order:
            header=header.strip().lower()
            if header not in ('from','subject','date','size'):continue
            value=sender_name(row) if header=='from' else row.get(header,'unavailable')
            if header=='size':
                value=(str(value)+' bytes') if isinstance(value,(int,float)) else str(value)
            parts.append((header.capitalize()+' ' if preferences.get('email_header_names',False) else '')+str(value))
        return '. '.join(parts)+'. '+str(number)+' of '+str(len(self.rows))+'.'


    def _focus(self, number):
        self.current = self._number(number)
        self.view = 'folder'
        self.body_text = ''
        self.body_offset = 0
        return self._summary(self.current) + ' Say open current message to read it.'

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
        self.current = number
        self.view = 'message'
        self.body_content=body or ''
        self.body_format=None
        import json
        try:format=json.loads((Path(self.folder)/'preferences.json').read_text()).get('email_format','html')
        except (OSError,ValueError):format='html'
        self.set_format(format)
        self.body_offset = 0
        self.reading.set_text(self.body_text, reset=True)
        return self._next_body()

    def set_format(self,format):
        if getattr(self,'body_format',None)==format or not hasattr(self,'body_content'):return
        self.body_format=format
        self.body_text=self.body_content if format=='html' else getattr(self.body_content,'plain',str(self.body_content))
        self.reading.set_text(self.body_text,reset=True)

    def _next_body(self):
        if not self.body_text:
            return 'No readable body.'
        result = self.reading.read().replace('End of text.', 'End of message.')
        self.body_offset = self.reading.continuation
        return result

    def response_context(self, action):
        if self.current is None or self.view != 'message':
            raise MailboxError('Open and read a numbered message first.')
        address, _ = account_token(self.provider, self.folder)
        row = self.rows[self.current - 1]
        context = self.client().response_context(row['id'], action, address, self.folder_id)
        context['source_address'] = address
        self._clear_marks()
        self.pending = None
        return context

    def process(self, text):
        spoken = text.strip().rstrip('.!?')
        command = spoken.lower()
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
                (Path(self.folder) / 'email-list-size.txt').write_text(value, encoding='utf-8')
            except OSError:
                return 'I could not save that email list setting. Try again.'
            self.previous_cursors.clear()
            result = self.list_messages()
            return ('All messages are available; I load 10 at a time as you say next message. ' if self.all_messages else
                    'Showing ' + value + ' messages at a time. ') + result
        if command in ('email list size', 'mail list size', 'how many emails', 'how many messages per page'):
            return ('All messages, loaded 10 at a time.' if self.all_messages else
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
            if command in ('next','previous') and self.rows:
                command += ' message'
            if self.view == 'message' and command in ('message details','who is this from','read message details'):
                return self._summary(self.current)
            request = reading_request(command)
            if self.view == 'message' and request:
                unit, direction = request
                result = (self.reading.read(direction == 'top').replace('End of text.', 'End of message.')
                          if unit == 'read' else self.reading.move(unit, direction))
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
                self.folder_id = 'INBOX' if self.provider == 'gmail' else 'inbox'
                self.previous_cursors.clear()
                self._clear_marks()
                return self.list_messages()
            if command in ('back to inbox', 'return to inbox'):
                self.folder_name = 'Inbox'
                self.folder_id = 'INBOX' if self.provider == 'gmail' else 'inbox'
                self.previous_cursors.clear()
                self._clear_marks()
                return self.list_messages()
            if command.startswith('go to ') and command not in ('go to main menu',):
                return self._open_named_folder(command[len('go to '):])
            if command in ('list messages', 'read message list', 'refresh messages'):
                self.previous_cursors.clear()
                return self.list_messages()
            if command in ('next messages', 'next page', 'next list', 'get more emails',
                           'more emails', 'show more emails', 'show more messages', 'get more messages'):
                if not self.next_cursor:
                    return 'There are no more messages on this list.'
                self.previous_cursors.append(self.page_cursor)
                return self.list_messages(self.next_cursor)
            if command in ('previous messages', 'previous page', 'previous list',
                           'go back a list', 'back a list', 'back one list', 'earlier emails'):
                if not self.previous_cursors:
                    return 'This is the first page of messages.'
                return self.list_messages(self.previous_cursors.pop())
            if command in open_selection and self.view == 'folder':
                return self._read(self.current) if self.current is not None else 'There is no highlighted message. Say list messages.'
            match = re.fullmatch(r'(?:read|open) message (' + NUMBER + r')', command)
            if match:
                return self._read(match[1])
            if command in ('read current message', 'open current message', 'repeat message',
                           'read message', 'open message', 'open that', 'read that',
                           'open it', 'read email', 'open email'):
                return self._read(self.current) if self.current else 'Say read message followed by a number first.'
            if command in ('list links','read links') or re.fullmatch(r'(?:open|activate) link (\d+)',command):
                import json
                try:format=json.loads((Path(self.folder)/'preferences.json').read_text()).get('email_format','html')
                except (OSError,ValueError):format='html'
                if self.view!='message':return 'Open a message first.'
                if format!='html':return 'Choose HTML under Options, Email, Email format to navigate links.'
                links=[span for span in getattr(self.body_text,'spans',[]) if span[2]=='link']
                if command in ('list links','read links'):
                    return '. '.join(str(i+1)+'. '+self.body_text[a:b] for i,(a,b,_,url) in enumerate(links)) or 'No links in this message.'
                number=int(command.split()[-1])
                if not 1<=number<=len(links):return 'That link number is not in this message.'
                import webbrowser
                webbrowser.open(links[number-1][3]);return 'Opening link '+str(number)+'.'
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
                if delta == 1 and self.current == len(self.rows):
                    if not self.next_cursor:
                        return 'This is the last message in this folder.'
                    cursor = self.next_cursor
                    self.previous_cursors.append(self.page_cursor)
                    self.list_messages(cursor)
                    return self._focus(1) if self.rows else 'No more messages were returned.'
                if delta == -1 and self.current == 1:
                    if not self.previous_cursors:
                        return 'This is the first message in this folder.'
                    self.list_messages(self.previous_cursors.pop())
                    return self._focus(len(self.rows)) if self.rows else 'No previous messages were returned.'
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
                    return 'Unmarked message '+str(self.current)+'.'
                self._mark(self.current)
                return 'Marked message '+str(self.current)+'.'
            if command=='select all messages':
                for number in range(1,len(self.rows)+1):self._mark(number)
                return str(len(self.rows))+' messages on this page selected.'
            if command in ('select', 'mark', 'select message', 'mark message', 'select this message', 'mark this message'):
                self._current_ids()
                self._mark(self.current)
                return 'Marked message ' + str(self.current) + '. ' + str(len(self.marked_ids)) + ' selected.'
            match = re.fullmatch(r'(?:select|mark) message (' + NUMBER + r')', command)
            if match:
                n = self._number(match[1])
                self._mark(n)
                return 'Selected message ' + str(n) + '. ' + str(len(self.marked_ids)) + ' selected.'
            if command in ('unmark message', 'deselect message', 'unselect message'):
                self._current_ids()
                self._unmark(self.current)
                return 'Unmarked message ' + str(self.current) + '. ' + str(len(self.marked_ids)) + ' selected.'
            match = re.fullmatch(r'select messages (' + NUMBER + r') (?:through|to) (' + NUMBER + r')', command)
            if match:
                first, last = self._number(match[1]), self._number(match[2])
                if first > last:
                    return 'Say the first number before the last number.'
                for n in range(first, last + 1): self._mark(n)
                return 'Selected messages ' + str(first) + ' through ' + str(last) + '. ' + str(len(self.marked_ids)) + ' selected.'
            if command in ('clear selection', 'cancel selection'):
                self._clear_marks()
                return 'Selection cleared.'
            if command in ('what is selected', 'read selection'):
                return ('Marked ' + str(len(self.marked_ids)) + ' messages across this folder.'
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
                self.folder_name, self.folder_id = 'Inbox', ('INBOX' if self.provider == 'gmail' else 'inbox')
                self.rows, self.current = [], None
                self._clear_marks()
                return 'Folder change completed. Back at Inbox. Say list folders to check it.'
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
                self._clear_marks()
                try:
                    if action == 'move':
                        self.client().move(ids, self.folder_id, destination)
                    else:
                        if self.provider == 'yahoo':
                            self.client().trash(ids, self.folder_id)
                        else:
                            self.client().trash(ids)
                except Exception:
                    self.rows = []
                    return 'Some messages may have moved. Refresh the folder and check before trying again.'
                count = str(len(ids)) + (' message ' if len(ids) == 1 else ' messages ')
                result = count + ('moved.' if action == 'move' else 'moved to Trash or Deleted Items.')
                try:
                    refreshed = self.list_messages(self.page_cursor)
                    if not self.rows and self.previous_cursors:
                        refreshed = self.list_messages(self.previous_cursors.pop())
                    return result + ' ' + refreshed
                except Exception:
                    self.rows = []
                    self.current = None
                    return result + ' The folder could not reload. Say list messages to try again.'
            if command in ('cancel', 'cancel move', 'cancel delete'):
                self.pending = None
                return 'Canceled. Nothing was changed.'
            return ('Say go to folder to choose from a list, next folder, previous folder, okay, back to inbox, '
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

    def _current_list(self):
        if not self.rows:
            return self.list_messages()
        parts = [self._summary(i) for i in range(1, len(self.rows) + 1)]
        return 'Back to ' + self.folder_name + '. ' + ' '.join(parts)


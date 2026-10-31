"""Locally saved email drafts using the same voice editor as documents."""
import html
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from email.message import EmailMessage, MIMEPart
from pathlib import Path

from document_editor import UNDO_REQUESTS, Paragraph, TextRun, VoiceDocument
from field_selection import FieldSelection, selection_command
from storage import keep_previous
from address_book import AddressBook
from ai_dictation import prepare_dictation


def email_field_destination(spoken):
    """Recognize short field navigation even with common speech homophones."""
    words = re.sub(r'[^a-z ]', ' ', spoken.casefold()).split()
    if not words:
        return None
    phrase = ' '.join(words)
    match = re.fullmatch(r'(?:(?:go|back|return|move|switch) (?:to|two|too) )(?:(?:the )?)(to|two|too|recipient|subject|body|message body|cc|bcc)(?: field)?|(?:the )?(to|two|too|recipient|subject|body|message body|cc|bcc) field', phrase)
    if not match:
        return None
    field = match[1] or match[2]
    return {'to':'recipient', 'two':'recipient', 'too':'recipient', 'message body':'body'}.get(field, field)


def normalize_recipient(spoken):
    """Accept a simple spoken email address, but refuse ambiguous recipients."""
    # Speech recognizers add sentence punctuation ("bob at example dot com.").
    address = spoken.strip().lower().rstrip('.!?,;').strip()
    address = re.sub(r'\s+at\s+', '@', address)
    address = re.sub(r'\s+dot\s+', '.', address)
    address = re.sub(r'\s+underscore\s+', '_', address)
    address = re.sub(r'\s+(?:dash|hyphen)\s+', '-', address)
    # Words after the domain ("bob at example dot com next week") must not be glued
    # onto it; a spoken name before the @ may still have spaces ("john smith at ...").
    if '@' in address and re.search(r'\s', address.rsplit('@', 1)[1].strip()):
        raise ValueError('I could not understand one complete email address. Say it like friend at example dot com.')
    address = re.sub(r'\s+', '', address)
    if (len(address) > 254 or not re.fullmatch(
            r'[a-z0-9][a-z0-9.!#$%&\x27*+/=?^_`{|}~-]*@[a-z0-9-]+(?:\.[a-z0-9-]+)+', address)
            or '..' in address or address.endswith('.')
            or any(part.startswith('-') or part.endswith('-')
                   for part in address.rsplit('@', 1)[-1].split('.'))):
        raise ValueError('I could not understand one complete email address. Say it like friend at example dot com.')
    return address


REPLY_SUBJECT = ('This response keeps the original subject. To change it, start a new email. '
                 'To keep writing your message, say go to body.')


def spoken_subject(text):
    """A subject as said: without the period speech recognizers put at the end ("Lunch plans.").
    A question mark, an exclamation mark, or dots (...) stay."""
    text = text.strip()
    if text.endswith('.') and not text.endswith('..'):
        text = text[:-1].rstrip()
    return text


def original_split(context):
    """(kept, dropped): the original message's files on a forward, and the ones taken off."""
    context = context or {}
    found = context.get('original_attachments') if context.get('action') == 'forward' else None
    if not isinstance(found, list):
        return [], []
    parts = [str(part) for part in context.get('dropped_parts', [])]
    names = list(context.get('dropped_attachments', []))
    kept, dropped = [], []
    # By part first: two files can share a name, and only the one chosen is taken off.
    for item in found:
        part = str(item.get('id', ''))
        if part and part in parts:
            parts.remove(part)
            if item.get('name') in names:
                names.remove(item['name'])
            dropped.append(item)
        else:
            kept.append(item)
    # Drafts saved before parts were kept name only the file.
    for item in list(kept):
        if item.get('name') in names:
            names.remove(item['name'])
            kept.remove(item)
            dropped.append(item)
    return kept, dropped


IMPORTANCE = ('high', 'low')
IMPORTANCE_COMMANDS = {
    'high importance': 'high', 'set importance to high': 'high', 'importance high': 'high', 'mark as high importance': 'high',
    'make it high importance': 'high', 'mark as important': 'high', 'mark it important': 'high', 'high priority': 'high',
    'low importance': 'low', 'set importance to low': 'low', 'importance low': 'low', 'mark as low importance': 'low',
    'make it low importance': 'low', 'low priority': 'low',
    'normal importance': '', 'set importance to normal': '', 'importance normal': '', 'mark as normal importance': '',
    'normal priority': '', 'clear importance': '', 'remove importance': '',
    'change importance': 'next',
}
IMPORTANCE_QUESTIONS = ('importance', 'what is the importance', 'read importance', 'what importance', 'read the importance')
RECEIPT_COMMANDS = {
    'request a read receipt': True, 'request read receipt': True, 'ask for a read receipt': True, 'read receipt on': True,
    'turn on read receipt': True, 'turn on the read receipt': True, 'add a read receipt': True, 'add read receipt': True,
    'return receipt on': True,
    'read receipt off': False, 'turn off read receipt': False, 'turn off the read receipt': False, 'no read receipt': False,
    'remove read receipt': False, 'remove the read receipt': False, 'cancel read receipt': False, 'return receipt off': False,
    'read receipt on or off': 'toggle', 'toggle read receipt': 'toggle',
}
# The phrases Control+Shift+I and Control+Shift+T send (input_parity.SCREEN_KEYS).
KEY_OPTIONS = ('change importance', 'read receipt on or off')
RECEIPT_QUESTIONS = ('read receipt', 'is there a read receipt', 'is a read receipt on', 'read receipt status')


def importance_words(level):
    return {'high': 'High importance', 'low': 'Low importance'}.get(level, 'Normal importance')


def receipt_words(on):
    return ('Read receipt on. The person is asked to tell you when they open it.' if on else 'No read receipt.')


@dataclass
class VoiceEmail(VoiceDocument):
    automatic_dictation: bool = True
    recipient: str = ''
    subject: str = ''
    provider: str = ''
    last_accepted_hash: str = ''
    last_attempt_hash: str = ''
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    compose_step: str = ''
    after_copy_step: str = ''
    response_context: dict = field(default_factory=dict)
    # Files from this computer: [{'path', 'name', 'size'}]. Read again when the email is sent.
    attachments: list = field(default_factory=list)
    # Importance ('high', 'low', or '' for normal) and a read receipt, as in Thunderbird's
    # Options menu while writing (Will, October 30, 2026).
    importance: str = ''
    read_receipt: bool = False
    EMPTY_NOTICE = 'The message body is empty.'
    EDGE_NAME = 'message'

    def __post_init__(self):
        super().__post_init__()
        if not self._opening:
            # File names drop punctuation, so "Dad's party" and "Dads party" would
            # share a file. A new draft never takes another draft's message or state file.
            numbered = re.fullmatch(r'(Untitled) (\d+)', self.title)
            base, number = (numbered[1], int(numbered[2]) + 1) if numbered else (self.title, 2)
            while self.path.exists() or self.state_path.exists():
                self.title = f'{base} {number}'
                number += 1
        self._discard_baseline = {}
        self._discard_attempt_hash = self.last_attempt_hash

    def _remember_draft_files(self):
        for path in (self.path, self.state_path):
            candidates = [path, path.with_suffix('.writing' + path.suffix)]
            candidates += [path.with_name(path.name + '.backup' + str(i)) for i in range(1, 4)]
            candidates += [path.with_name(path.name + '.backup-writing')]
            for candidate in candidates:
                if candidate not in self._discard_baseline:
                    self._discard_baseline[candidate] = candidate.read_bytes() if candidate.exists() else None

    def discard(self):
        # A send attempt cannot be undone by deleting its local evidence.
        if self.last_attempt_hash and self.last_attempt_hash != self._discard_attempt_hash:
            return False
        for path, original in self._discard_baseline.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        self._discard_baseline.clear()
        return True

    def send_hash(self):
        content = [self.provider, self.recipient, self.subject,
                   self.cc, self.bcc, self.response_context, [asdict(p) for p in self.paragraphs]]
        if self.attachments:
            # Only when there are files, so drafts saved before attachments keep their fingerprint.
            content.append([[item.get('path'), item.get('name'), item.get('size')] for item in self.attachments])
        if self.importance or self.read_receipt:
            # Likewise only when set: a change after the review needs a new review.
            content.append({'importance': self.importance, 'read_receipt': self.read_receipt})
        return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode('utf-8')).hexdigest()

    # Attachments ----------------------------------------------------------

    def attach(self, paths):
        """Attach files from this computer. Returns what to say. Folders and missing files are refused."""
        from mail_attachments import AttachmentError, MAX_DRAFT_TOTAL, count_words, limit_warning, local_attachment, size_words, total_size
        found = []
        for path in paths:
            item = local_attachment(path)
            if any(os.path.normcase(str(Path(old['path']))) == os.path.normcase(str(Path(item['path'])))
                   for old in self.attachments + found):
                continue
            found.append(item)
        if not found:
            if paths:
                raise AttachmentError(Path(paths[0]).name + ' is already attached.')
            raise AttachmentError('Choose a file to attach.')
        total = total_size(self.attachments + found)
        if total > MAX_DRAFT_TOTAL:
            raise AttachmentError('That would make this email ' + size_words(total) + '. That is more than email can carry. '
                                  'Nothing was attached. Share large files another way, such as a cloud folder.')
        self.attachments.extend(found)
        self.save()
        from mail_attachments import joined
        said = 'Attached ' + joined(item['name'] for item in found) + '. ' + count_words(len(self.all_attachments())) + '.'
        return said + limit_warning(total)

    def remove_attachment(self, index):
        item = self.attachments.pop(index)
        self.save()
        return item

    def original_attachments(self):
        """The original message's files that go with this forward, less the ones taken off."""
        return original_split(self.response_context)[0]

    def dropped_originals(self):
        """The original message's files the person took off this forward."""
        return original_split(self.response_context)[1]

    def all_attachments(self):
        """Every file this email will carry: the forwarded originals first, then files from this computer."""
        return [dict(item, original=True) for item in self.original_attachments()] + list(self.attachments)

    def drop_original(self, name, part=None):
        self.response_context.setdefault('dropped_attachments', []).append(name)
        if part is not None and str(part):
            self.response_context.setdefault('dropped_parts', []).append(str(part))
        self.save()

    def _attachment_part(self, item):
        """The attachment as ready MIME bytes, encoded once and kept while the file is unchanged.

        Encoding a large file takes seconds; a draft is saved after every sentence."""
        from mail_attachments import mime_type
        path = Path(item['path'])
        info = path.stat()
        key = (str(path), info.st_size, info.st_mtime_ns, item['name'])
        cache = self.__dict__.setdefault('_attachment_parts', {})
        if key not in cache:
            maintype, subtype = mime_type(item['name'])
            part = MIMEPart()
            part.set_content(path.read_bytes(), maintype=maintype, subtype=subtype, disposition='attachment',
                             filename=item['name'])
            for old in [k for k in cache if k[0] == key[0]]:
                del cache[old]
            cache[key] = part.as_bytes()
        return cache[key]

    def send_option(self, command):
        """Importance and read receipt. Only the whole phrase counts, so a sentence such as
        "This is of high importance." stays in the message."""
        command = re.sub(r'[,;]', '', command).strip()
        if self.compose_step == 'subject' and command not in KEY_OPTIONS:
            # In the Subject box, words such as "High priority" are the subject (writing comes
            # first). Control+Shift+I and Control+Shift+T still work there.
            return ''
        level = IMPORTANCE_COMMANDS.get(command)
        if level == 'next':
            order = ('', 'high', 'low')
            level = order[(order.index(self.importance) + 1) % len(order)]
        if level is not None:
            self.importance = level
            self.save()
            return importance_words(level) + '.'
        if command in IMPORTANCE_QUESTIONS:
            return importance_words(self.importance) + '. ' + receipt_words(self.read_receipt)
        receipt = RECEIPT_COMMANDS.get(command)
        if receipt == 'toggle':
            receipt = not self.read_receipt
        if receipt is not None:
            self.read_receipt = receipt
            self.save()
            return receipt_words(receipt)
        if command in RECEIPT_QUESTIONS:
            return receipt_words(self.read_receipt)
        return ''

    def _undo_extra(self):
        # Undo brings back the addresses, subject and account with the message text.
        return (self.recipient, self.subject, self.provider, list(self.cc), list(self.bcc),
                bool(self.response_context.get('recipient_override')))

    def _restore_extra(self, extra):
        if extra is None:
            return
        self.recipient, self.subject, self.provider, cc, bcc, override = extra
        self.cc, self.bcc = list(cc), list(bcc)
        if self.response_context:
            if override: self.response_context['recipient_override'] = True
            else: self.response_context.pop('recipient_override', None)

    def move_to_account(self, provider):
        """Send this draft from another kind of account (Gmail and Microsoft were removed).

        A reply or forward cannot point at a message in the removed mailbox, so it
        becomes a new email to the same people with the same subject. A forward
        keeps the forwarded text, added at the end so the review reads it."""
        context = self.response_context or {}
        if context.get('action') == 'forward' and (context.get('original_from') or context.get('original_body')):
            lines = ['Forwarded message:', 'From: ' + str(context.get('original_from', '')),
                     'Subject: ' + str(context.get('original_subject', ''))]
            body = str(context.get('original_body', '') or '')
            lines += [part.strip() for part in re.split(r'\n\s*\n', body) if part.strip()]
            self.paragraphs = [p for p in self.paragraphs if p.text.strip()] + [Paragraph(text=line) for line in lines]
        self.response_context = {}
        self.provider = provider
        self.save()

    @property
    def path(self):
        safe = re.sub(r'[^\w .-]+', '', self.title).strip(' .')[:80] or 'Untitled'
        return self.folder / (safe + '.eml')

    @property
    def state_path(self):
        return self.path.with_suffix('.json')

    def save(self):
        self._remember_draft_files()
        self.folder.mkdir(parents=True, exist_ok=True)
        for p in self.paragraphs:
            self.reconcile_runs(p)
        state = {
            'title': self.title, 'recipient': self.recipient, 'subject': self.subject,
            'provider': self.provider, 'last_accepted_hash': self.last_accepted_hash,
            'last_attempt_hash': self.last_attempt_hash,
            'cc': self.cc, 'bcc': self.bcc, 'response_context': self.response_context,
            'attachments': self.attachments,
            'importance': self.importance, 'read_receipt': self.read_receipt,
            'paragraphs': [asdict(p) for p in self.paragraphs],
        }
        temporary = self.state_path.with_suffix('.writing.json')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        keep_previous(self.state_path)
        temporary.replace(self.state_path)
        msg = EmailMessage()
        msg['To'] = self.recipient
        if self.cc: msg['Cc'] = ', '.join(self.cc)
        if self.bcc: msg['Bcc'] = ', '.join(self.bcc)
        msg['Subject'] = self.subject
        if self.send_hash() == self.last_attempt_hash:
            msg['X-VoiceCompanion-Send-Attempted'] = '1'
        else:
            msg['X-Unsent'] = '1'
        body = '\n\n'.join(p.text for p in self.paragraphs)
        if self.response_context.get('action') == 'forward' and (
                self.response_context.get('original_from') or self.response_context.get('original_body')):
            body += ('\n\nForwarded message:\nFrom: ' + self.response_context.get('original_from', '') +
                     '\nSubject: ' + self.response_context.get('original_subject', '') +
                     '\n\n' + self.response_context.get('original_body', ''))
        msg.set_content(body)
        from email_formatting import formatted_body
        msg.add_alternative(formatted_body(self), subtype='html')
        parts = []
        for item in self.attachments:
            try:
                parts.append(self._attachment_part(item))
            except OSError:
                # Gone or locked: the draft keeps the file's place, and sending says what is wrong.
                pass
        data = msg.as_bytes()
        if parts:
            # The files' ready bytes go in as they are, so a large file is not encoded again.
            import uuid
            boundary = self.__dict__.setdefault('_mixed_boundary', '===============' + uuid.uuid4().hex + '==')
            msg.make_mixed()
            msg.set_boundary(boundary)
            data = msg.as_bytes()
            end = data.rindex(b'--' + boundary.encode('ascii') + b'--')
            data = (data[:end] + b''.join(b'--' + boundary.encode('ascii') + b'\n' + part + b'\n' for part in parts)
                    + b'--' + boundary.encode('ascii') + b'--\n')
        output = self.path.with_suffix('.writing.eml')
        output.write_bytes(data)
        output.replace(self.path)
        return self.path

    @classmethod
    def open_existing(cls, folder: Path, name: str):
        safe = re.sub(r'[^\w .-]+', '', name).strip(' .')[:80]
        candidates = [p for p in folder.glob('*.json') if p.stem.casefold() == safe.casefold()] if folder.exists() else []
        if not candidates:
            raise FileNotFoundError(name)
        if candidates[0].stat().st_size > 10 * 1024 * 1024:
            raise ValueError('This email draft is too large for this editor.')
        data = json.loads(candidates[0].read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('paragraphs', []), list):
            raise ValueError('This email draft has invalid saved data.')
        if any(not isinstance(data.get(field, ''), str) for field in ('recipient', 'subject', 'provider', 'last_accepted_hash', 'last_attempt_hash')):
            raise ValueError('This email draft has invalid address or subject data.')
        if any(not isinstance(data.get(key, []), list) or any(not isinstance(a, str) for a in data.get(key, []))
               for key in ('cc', 'bcc')):
            raise ValueError('This email draft has invalid CC or BCC data.')
        if not isinstance(data.get('response_context', {}), dict):
            raise ValueError('This email draft has invalid response data.')
        attached = data.get('attachments', [])
        if not isinstance(attached, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('name'), str)
                or not isinstance(item.get('size'), int) or set(item) - {'path', 'name', 'size'} for item in attached):
            raise ValueError('This email draft has invalid attachment data.')
        paragraphs = data.get('paragraphs', [])
        if any(not isinstance(p, dict) or not isinstance(p.get('text'), str)
               or p.get('kind', 'Normal') not in ('Normal', 'Bulleted list', 'Numbered list',
                                                  *(f'Heading {n}' for n in range(1, 7)))
               or not isinstance(p.get('bold', False), bool)
               or not isinstance(p.get('italic', False), bool)
               for p in paragraphs):
            raise ValueError('This email draft has invalid paragraph data.')
        draft = cls(folder, title=candidates[0].stem, _opening=True)
        draft.recipient = data.get('recipient', '')
        draft.subject = data.get('subject', '')
        draft.provider = data.get('provider', '')
        draft.last_accepted_hash = data.get('last_accepted_hash', '')
        draft.last_attempt_hash = data.get('last_attempt_hash', '')
        draft._discard_attempt_hash = draft.last_attempt_hash
        draft.cc = data.get('cc', [])
        draft.bcc = data.get('bcc', [])
        draft.compose_step = 'recipient' if not draft.recipient else 'subject' if not draft.subject else 'body'
        draft.response_context = data.get('response_context', {})
        draft.attachments = [dict(item) for item in attached]
        # A damaged or unknown value is normal importance, never a refusal to open the draft.
        draft.importance = data.get('importance') if data.get('importance') in IMPORTANCE else ''
        draft.read_receipt = data.get('read_receipt') is True
        names = {f.name for f in fields(Paragraph)}
        for p in paragraphs:
            if set(p) - names or not isinstance(p.get('runs', []), list) or any(
                    not isinstance(run, dict) or not isinstance(run.get('text'), str)
                    or set(run) - {f.name for f in fields(TextRun)} for run in p.get('runs', [])):
                raise ValueError('This email draft has invalid formatted text.')
        draft.paragraphs = [Paragraph(**{**p, 'runs': [TextRun(**run) for run in p.get('runs', [])]})
                            for p in paragraphs]
        return draft

    def apply_field_selection_edit(self, field, value):
        self.checkpoint({'recipient':'the To address','subject':'the subject','cc':'the CC addresses','bcc':'the BCC addresses'}.get(field))
        if field == 'recipient': self.recipient = value
        elif field == 'subject': self.subject = value
        elif field in ('cc','bcc'): setattr(self,field,[a.strip() for a in value.split(',') if a.strip()])
        else: return
        if self.response_context: self.response_context['recipient_override'] = True
        self.save()

    def process(self, raw, typed=False):
        """typed: from the keyboard. Enter in the To box goes on to the Subject box, as in a form."""
        if selection_command(raw) and self.compose_step in ('recipient','subject','cc','bcc'):
            field = self.compose_step
            value = self.recipient if field == 'recipient' else self.subject if field == 'subject' else ', '.join(getattr(self,field))
            if not hasattr(self, '_field_selections'): self._field_selections = {}
            selector = self._field_selections.setdefault(field, FieldSelection())
            result = selector.select(value, raw)[0]
            if selector.edited_text is not None:
                self.apply_field_selection_edit(field,selector.edited_text)
            return result
        text = raw.strip()
        command = text.lower().rstrip('.!?').strip()
        options = self.send_option(command)
        if options:
            return options
        if command in ('save email', 'save draft', 'finish email', 'save document', 'save my document', 'finish document', 'save', 'save it'):
            self.dictating = False
            self.save()
            self._discard_baseline.clear()
            self._discard_attempt_hash = self.last_attempt_hash
            return 'Email draft saved locally as ' + self.title + '. It has not been sent.'
        field = email_field_destination(command)
        if field:
            if self.response_context and field == 'subject':
                self.compose_step='subject'
                return 'Subject field. '+self.subject+'. '+REPLY_SUBJECT
            if field in ('cc','bcc'): self.after_copy_step='body'
            self.compose_step=field
            if field=='recipient':
                return 'To field. ' + ('Current address: '+self.recipient+'. ' if self.recipient else '') + 'Say or type the full email address to replace it.'
            if field=='subject':
                return 'Subject field. ' + ('Current subject: '+self.subject+'. ' if self.subject else '') + 'Say or type the subject to replace it.'
            if field in ('cc','bcc'):
                return field.upper()+' field. '+('Current addresses: '+', '.join(getattr(self,field))+'. ' if getattr(self,field) else '')+'Say or type an address to add, or say skip.'
            return 'Message body. Speak or type your message. Say send it when ready.'
        if command in ('start dictation','dictate','continue writing'):
            self.compose_step = 'body'
        def contact_address(value):
            from mail_accounts import ProtectedStore
            selected = ProtectedStore(self.folder.parent).selected()
            account = selected[1] if selected and selected[0] == self.provider else ''
            # The contacts saved here, then Thunderbird's address books (contacts_book.py).
            from contacts_book import address_for
            return address_for(self.folder.parent, account, value) or value
        def parses(value):
            try: normalize_recipient(contact_address(value)); return True
            except ValueError: return False
        # While writing the message body, a sentence that merely starts like a field
        # command ("Send to the team at the office.", "CC is fine with me.") is dictation.
        in_body = self.compose_step == 'body'
        if command in ('cc', 'bcc', 'add cc', 'add bcc'):
            field = 'bcc' if 'bcc' in command else 'cc'
            self.after_copy_step = self.compose_step or 'body'
            self.compose_step = field
            return 'Who should receive the ' + field.upper() + ' copy? Say the email address, or say skip.'
        if command in ('no subject','skip subject','leave subject blank') or command=='skip' and self.compose_step=='subject':
            if self.response_context:return 'This response keeps the original subject.'
            self.checkpoint('the subject');self.subject='';self.compose_step='body';self.save()
            return 'No subject. What would you like the message to say?'
        if command in ('skip','skip cc','skip bcc') and self.compose_step in ('cc','bcc'):
            self.compose_step = self.after_copy_step or 'body'
            return 'Skipped. ' + ('What is the subject?' if self.compose_step == 'subject' else 'What would you like the message to say?')
        copy_match = re.fullmatch(r'(?:add |set )?(cc|bcc)(?: to| is)? (.+)', text, re.I)
        if copy_match and in_body and not parses(copy_match.group(2)):
            copy_match = None
        if copy_match or self.compose_step in ('cc','bcc'):
            field = copy_match.group(1).lower() if copy_match else self.compose_step
            try: address = normalize_recipient(contact_address(copy_match.group(2) if copy_match else text))
            except ValueError as exc: return str(exc) + ' Say skip to leave this field blank.'
            self.checkpoint('the ' + field.upper() + ' address')
            values = getattr(self, field)
            if address not in values: values.append(address)
            if self.response_context: self.response_context['recipient_override'] = True
            self.save()
            self.compose_step = self.after_copy_step or ('subject' if not self.subject else 'body')
            return field.upper() + ' recorded as ' + address + '. ' + ('What is the subject?' if self.compose_step == 'subject' else 'What would you like the message to say?')
        # A message is ready for natural body speech. Exact editor commands
        # still take precedence in VoiceDocument.process.
        if command.startswith(('type literally ', 'dictate literally ')):
            literal = re.sub(r'^(?:type|dictate) literally\s+', '', text, flags=re.I)
            return self.append_text(literal, literal=True)
        if command.startswith('use yahoo'):
            provider = command.split()[1]
            self.checkpoint('the email account')
            self.provider = provider
            self.save()
            return provider.capitalize() + ' selected for this local draft. Ask your helper to confirm the account is connected before sending.'
        if command in ('which email provider', 'read email provider'):
            return 'Email provider: ' + (self.provider or 'not selected.')
        if command.startswith(('email to ', 'send to ', 'recipient is ')) and not (
                in_body and not parses(re.sub(r'^(email to |send to |recipient is )', '', text, flags=re.I).strip())):
            spoken = re.sub(r'^(email to |send to |recipient is )', '', text, flags=re.I).strip()
            try:
                recipient = normalize_recipient(contact_address(spoken))
            except ValueError as exc:
                return str(exc)
            self.checkpoint('the To address')
            self.recipient = recipient
            if self.response_context.get('action') in ('reply', 'reply_all'):
                self.response_context['recipient_override'] = True
            # Said: a subject already given stays, and the next words go to the message, never
            # over the subject. Typed: on to the Subject box, which shows the subject to edit.
            self.compose_step = 'body' if self.response_context or self.subject and not typed else 'subject'
            self.save()
            if self.compose_step == 'body':
                return 'Recipient recorded as ' + recipient + '. Back to the message body.'
            return 'Recipient recorded as ' + recipient + '. What is the subject? You can also say CC or BCC first.'
        # In the message, "Subject is ..." is words (writing comes first), unless nothing is written
        # yet and there is no subject: then it can only mean the subject (sweep, October 31, 2026).
        body_started = bool(self.subject) or any(p.text.strip() for p in self.paragraphs)
        if command.startswith('subject is ') and not (in_body and (self.response_context or body_started)):
            if self.response_context:
                # A reply keeps its subject: in the message, "Subject is ..." is only words (above).
                return REPLY_SUBJECT
            if '\n' in text or '\r' in text:
                return 'Please say the subject on one line.'
            subject = spoken_subject(prepare_dictation(text[len('subject is '):], old_school_cleanup=False))
            if '\n' in subject or '\r' in subject:
                return 'Please say the subject on one line.'
            self.checkpoint('the subject')
            self.subject = subject
            self.save()
            self.compose_step = 'body'
            return 'Subject recorded as ' + self.subject + '. What would you like the message to say?'
        if self.compose_step == 'recipient' and text and command not in UNDO_REQUESTS and not command.startswith(('read ', 'help ', 'save ', 'name ', 'use ', 'start dictation', 'pause dictation', 'undo')):
            return self.process('email to ' + text, typed)
        if self.compose_step == 'subject' and text and command not in UNDO_REQUESTS and not command.startswith(('read ', 'help ', 'save ', 'name ', 'use ', 'start dictation', 'pause dictation', 'undo')):
            return self.process('subject is ' + text)
        if command in ('read recipient', 'who is this to'):
            return 'Recipient: ' + (self.recipient or 'not entered.')
        if command in ('read subject', 'what is the subject'):
            return 'Subject: ' + (self.subject or 'not entered.')
        if command in ('read cc','read bcc'):
            field = 'bcc' if command.endswith('bcc') else 'cc'
            return field.upper() + ': ' + (', '.join(getattr(self, field)) or 'none.')
        if command in ('send email', 'send message'):
            self.dictating = False
            self.save()
            return 'This test cannot send email yet. I saved the draft locally. It has not been sent.'
        if command in ('help with email', 'help with emails'):
            return 'Say email to followed by an address, subject is followed by a subject, then speak your message. Say read paragraph, replace word, undo, redo, or save draft. Say send it to hear the full review, then yes to send or cancel send. To include command words in your message, say type literally followed by the words.'
        if command.startswith(('name document ', 'title is ')):
            return 'Use subject is followed by the email subject. The draft has its own saved name.'
        response = super().process(raw)
        if self.automatic_dictation and response.startswith('I did not recognize that document request.'):
            self.dictating = True
            return super().process(raw)
        return response

"""Locally saved email drafts using the same voice editor as documents."""
import html
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from email.message import EmailMessage
from pathlib import Path

from document_editor import Paragraph, TextRun, VoiceDocument
from field_selection import FieldSelection, selection_command
from storage import keep_previous
from address_book import AddressBook


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
    address = spoken.strip().lower()
    address = re.sub(r'\s+at\s+', '@', address)
    address = re.sub(r'\s+dot\s+', '.', address)
    address = re.sub(r'\s+', '', address)
    if (len(address) > 254 or not re.fullmatch(
            r'[a-z0-9][a-z0-9.!#$%&\x27*+/=?^_`{|}~-]*@[a-z0-9-]+(?:\.[a-z0-9-]+)+', address)
            or '..' in address or address.endswith('.')
            or any(part.startswith('-') or part.endswith('-')
                   for part in address.rsplit('@', 1)[-1].split('.'))):
        raise ValueError('I could not understand one complete email address. Say it like friend at example dot com.')
    return address


@dataclass
class VoiceEmail(VoiceDocument):
    automatic_dictation: bool = True
    recipient: str = ''
    subject: str = ''
    provider: str = ''
    last_metadata: tuple | None = None
    last_accepted_hash: str = ''
    last_attempt_hash: str = ''
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    compose_step: str = ''
    after_copy_step: str = ''
    response_context: dict = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
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
        return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode('utf-8')).hexdigest()

    def checkpoint(self):
        super().checkpoint()
        self.last_metadata = (self.recipient, self.subject, self.provider)

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
        if self.response_context.get('action') == 'forward':
            body += ('\n\nForwarded message:\nFrom: ' + self.response_context.get('original_from', '') +
                     '\nSubject: ' + self.response_context.get('original_subject', '') +
                     '\n\n' + self.response_context.get('original_body', ''))
        msg.set_content(body)
        from email_formatting import formatted_body
        msg.add_alternative(formatted_body(self), subtype='html')
        output = self.path.with_suffix('.writing.eml')
        output.write_bytes(msg.as_bytes())
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
        self.checkpoint()
        if field == 'recipient': self.recipient = value
        elif field == 'subject': self.subject = value
        elif field in ('cc','bcc'): setattr(self,field,[a.strip() for a in value.split(',') if a.strip()])
        else: return
        if self.response_context: self.response_context['recipient_override'] = True
        self.save()

    def process(self, raw):
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
        if command in ('save email', 'save draft', 'finish email', 'save document', 'finish document'):
            self.dictating = False
            self.save()
            self._discard_baseline.clear()
            self._discard_attempt_hash = self.last_attempt_hash
            return 'Email draft saved locally as ' + self.title + '. It has not been sent.'
        field = email_field_destination(command)
        if field:
            if self.response_context and field == 'subject':
                self.compose_step='subject'
                return 'Subject field. '+self.subject+'. This response keeps the original subject. Start a new email to change it.'
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
            entry = AddressBook(self.folder.parent, account).get(value)
            return entry['address'] if entry else value
        if command in ('cc', 'bcc', 'add cc', 'add bcc'):
            field = 'bcc' if 'bcc' in command else 'cc'
            self.after_copy_step = self.compose_step or 'body'
            self.compose_step = field
            return 'Who should receive the ' + field.upper() + ' copy? Say the email address, or say skip.'
        if command in ('no subject','skip subject','leave subject blank') or command=='skip' and self.compose_step=='subject':
            if self.response_context:return 'This response keeps the original subject.'
            self.checkpoint();self.subject='';self.compose_step='body';self.save()
            return 'No subject. What would you like the message to say?'
        if command in ('skip','skip cc','skip bcc') and self.compose_step in ('cc','bcc'):
            self.compose_step = self.after_copy_step or 'body'
            return 'Skipped. ' + ('What is the subject?' if self.compose_step == 'subject' else 'What would you like the message to say?')
        copy_match = re.fullmatch(r'(?:add |set )?(cc|bcc)(?: to| is)? (.+)', text, re.I)
        if copy_match or self.compose_step in ('cc','bcc'):
            field = copy_match.group(1).lower() if copy_match else self.compose_step
            try: address = normalize_recipient(contact_address(copy_match.group(2) if copy_match else text))
            except ValueError as exc: return str(exc) + ' Say skip to leave this field blank.'
            self.checkpoint()
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
        if command.startswith(('use gmail', 'use outlook', 'use yahoo')):
            provider = command.split()[1]
            self.checkpoint()
            self.provider = provider
            self.save()
            return provider.capitalize() + ' selected for this local draft. Ask your helper to confirm the account is connected before sending.'
        if command in ('which email provider', 'read email provider'):
            return 'Email provider: ' + (self.provider or 'not selected.')
        if command.startswith(('email to ', 'send to ', 'recipient is ')):
            spoken = re.sub(r'^(email to |send to |recipient is )', '', text, flags=re.I).strip()
            try:
                recipient = normalize_recipient(contact_address(spoken))
            except ValueError as exc:
                return str(exc)
            self.checkpoint()
            self.recipient = recipient
            if self.response_context.get('action') in ('reply', 'reply_all'):
                self.response_context['recipient_override'] = True
            self.compose_step = 'body' if self.response_context else 'subject'
            self.save()
            if self.response_context:
                return 'Recipient recorded as ' + recipient + '. Back to the message body.'
            return 'Recipient recorded as ' + recipient + '. What is the subject? You can also say CC or BCC first.'
        if command.startswith('subject is '):
            if self.response_context:
                return 'This subject comes from the original message. Start a new email for a different subject.'
            if '\n' in text or '\r' in text:
                return 'Please say the subject on one line.'
            self.checkpoint()
            self.subject = text[len('subject is '):].strip()
            self.save()
            self.compose_step = 'body'
            return 'Subject recorded as ' + self.subject + '. What would you like the message to say?'
        if self.compose_step == 'recipient' and text and not command.startswith(('read ', 'help ', 'save ', 'name ', 'use ', 'start dictation', 'pause dictation', 'undo')):
            return self.process('email to ' + text)
        if self.compose_step == 'subject' and text and not command.startswith(('read ', 'help ', 'save ', 'name ', 'use ', 'start dictation', 'pause dictation', 'undo')):
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
        if command in ('undo', 'undo that', 'undo last change'):
            previous = self.last_metadata
            response = super().process(raw)
            if previous is not None and response.startswith('Undid'):
                self.recipient, self.subject, self.provider = previous
                self.last_metadata = None
                self.save()
            return response
        if command in ('help with email', 'help with emails'):
            return 'Say email to followed by an address, subject is followed by a subject, then speak your message. Say read paragraph, replace word, undo, or save draft. Say send it to hear the full review, then yes to send or cancel send. To include command words in your message, say type literally followed by the words.'
        if command.startswith(('name document ', 'title is ')):
            return 'Use subject is followed by the email subject. The draft has its own saved name.'
        response = super().process(raw)
        if self.automatic_dictation and response.startswith('I did not recognize that document request.'):
            self.dictating = True
            return super().process(raw)
        return response

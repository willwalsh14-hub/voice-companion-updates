"""Email attachments the way a person uses them: keys, voice and the screens.

- In email: hear a message's files, open the Attachments list, move, save, open.
- In a draft: Control+O opens File manager as a picker, Enter attaches and comes back.
- In File manager: Control+N emails the chosen files, Control+O adds them to the open draft.
- Control+T, the old attach key, still works and says the new key once.
- The send review names the files; a file gone before sending stops the send.
- Thunderbird end to end: the real add-on in Node, the real bridge, real bytes.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import attachment_screen
import companion as app
from document_editor import Paragraph
from email_draft import VoiceEmail
from field_selection import FieldSelection
from file_manager import FileManager
from mail_voice import MailSession
from reading_navigation import ReadingCursor
from test_file_manager import FakeFiles, settle

REPORT = b'%PDF-1.4 ' + bytes(range(256)) * 40
PHOTO = b'\xff\xd8 photo bytes'
PROGRAM = b'MZ program'


class AttachmentMailbox:
    """Behaves like ThunderbirdMailbox or YahooMailbox for one inbox with files."""
    files = {'m1': [('1.2', 'report.pdf', REPORT), ('1.3', 'photo.jpg', PHOTO), ('1.4', 'setup.exe', PROGRAM)], 'm2': []}
    def __init__(self, *args): pass
    def folders(self): return [('Inbox', 'inbox')]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': 'm1', 'from': 'Sam <sam@example.com>', 'subject': 'Files'},
                 {'id': 'm2', 'from': 'Ann <ann@example.com>', 'subject': 'Plain'}], None)
    def rows(self, ident):
        return [{'id': part, 'name': name, 'size': len(data), 'type': 'application/octet-stream'} for part, name, data in self.files[ident]]
    def read_message(self, ident, folder_id=None):
        from email_reader import plain_body
        body = plain_body('Hello from Sam.')
        body.attachments = self.rows(ident)
        return body
    def attachments(self, ident, folder_id=None): return self.rows(ident)
    def attachment_chunks(self, ident, attachment, folder_id=None):
        data = next(data for part, _, data in self.files[ident] if part == attachment['id'])
        for start in range(0, len(data), 1000):
            yield data[start:start + 1000]
        if not data:
            yield b''
    def response_context(self, ident, action, address, folder_id=None):
        return {'action': action, 'recipient': '' if action == 'forward' else 'sam@example.com', 'cc': [],
                'subject': 'Fwd: Files', 'source_id': ident, 'original_attachments': self.rows(ident) if action == 'forward' else None}


class FlowTestCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.downloads = self.root / 'Downloads'; self.downloads.mkdir()
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults = {'APP': self.root / 'data', 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'help_session': None, 'tutorial_session': None, 'MEDIA_HUB': None, 'note_buffer': [],
                    'note_selection': FieldSelection(), 'note_reading': ReadingCursor(), 'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {},
                    'PREFERENCES': dict(app.PREFERENCES, download_folder=''), 'SLEEP_RETURN_MODE': 'awake',
                    'FILE_READER': None, 'FILE_MANAGER': None, 'TASK_NAV': None, 'SCREEN_MENU': None,
                    'ACCOUNT_ENTRY': None, 'CONFIRM_CHOICE': 'yes', 'ACCOUNT_PICKER': None, 'THUNDERBIRD_SETUP': None}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        (self.root / 'data').mkdir()
        self.said = []
        speak = lambda text, *a, **k: self.said.append(str(text))
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            self.stack.enter_context(patch.object(app, name, side_effect=speak))
        self.stack.enter_context(patch.object(app, 'interrupt_speech'))
        self.stack.enter_context(patch.object(app, 'finish_mail_announcement'))
        self.stack.enter_context(patch('mail_voice.account_token', return_value=('me@example.com', 'token')))
        self.stack.enter_context(patch('downloads.default_folder', return_value=self.downloads))
        self.started = []
        self.stack.enter_context(patch('os.startfile', side_effect=lambda path: self.started.append(path), create=True))
        attachment_screen.PENDING = None
        attachment_screen.ITEMS = []
        # File manager on a folder of real files (the Windows Shell is replaced by FakeFiles).
        self.files = self.root / 'My files'; self.files.mkdir()
        (self.files / 'letter.txt').write_text('Dear Sam.', encoding='utf-8')
        (self.files / 'photo.jpg').write_bytes(PHOTO)
        (self.files / 'Old letters').mkdir()
        real = FileManager
        made = lambda backend=None: real(backend or FakeFiles(self.files))
        self.stack.enter_context(patch('file_manager.FileManager', made))
        self.stack.enter_context(patch.object(app, 'FileManager', made))
        self.stack.enter_context(patch('file_manager.start_location', lambda setting: str(self.files)))

    def press(self, key, mode):
        request = app.navigation_key(key, mode)
        return app.handle_keyboard(request, mode) if request else mode

    def last(self):
        return self.said[-1]

    def inbox(self):
        app.mail_session = MailSession('thunderbird', str(self.root / 'data'), AttachmentMailbox)
        app.mail_session.process('open inbox')
        return 'mailbox'

    def draft(self, **fields):
        draft = VoiceEmail(self.root / 'data' / 'Email Drafts', **{'title': 'Note', 'recipient': 'sam@example.com',
                                                                   'subject': 'Hi', 'provider': 'yahoo', 'compose_step': 'body', **fields})
        draft.paragraphs = [Paragraph('See the files.')]
        draft.save()
        app.email_draft = draft
        return draft

    def settle_files(self, mode):
        settle(app.FILE_MANAGER)
        return mode


class MessageAttachmentTests(FlowTestCase):
    def test_reading_says_the_files_and_l_lists_them(self):
        mode = self.inbox()
        self.assertEqual(self.press('Enter', mode), 'mailbox')
        self.assertTrue(self.last().startswith('Message opened. 3 attachments: report.pdf, 10 kilobytes; photo.jpg, 14 bytes; setup.exe, 10 bytes. '
                                               'Press L to list them. Hello from Sam.'), self.last())
        mode = self.press('L', mode)
        self.assertEqual(mode, 'attachments')
        self.assertEqual(self.last(), 'Attachments, 3 files. report.pdf, 10 kilobytes, 1 of 3.')
        self.press('Up', mode)
        self.assertEqual(self.last(), 'Start of list. report.pdf, 10 kilobytes, 1 of 3.')
        self.press('Down', mode)
        self.assertEqual(self.last(), 'photo.jpg, 14 bytes, 2 of 3.')
        self.press('End', mode)
        self.press('Down', mode)
        self.assertEqual(self.last(), 'End of list. setup.exe, 10 bytes, 3 of 3.')
        self.press('Home', mode)
        # Escape goes back to the message, where you were.
        self.assertEqual(self.press('Escape', mode), 'mailbox')
        self.assertEqual(self.last(), 'Back to message.')
        self.assertEqual(app.mail_session.view, 'message')

    def test_voice_and_keys_open_the_same_list(self):
        mode = self.inbox()
        self.assertEqual(app.handle('List attachments.', mode), 'attachments')
        voice = self.last()
        self.press('Escape', 'attachments')
        self.assertEqual(self.press('L', mode), 'attachments')
        self.assertEqual(self.last(), voice)
        # A message with no files says so and stays put.
        app.handle('next message', 'mailbox')
        self.assertEqual(self.press('L', 'mailbox'), 'mailbox')
        self.assertEqual(self.last(), 'This message has no attachments.')

    def test_save_says_where_never_overwrites_and_save_all(self):
        mode = self.press('L', self.inbox())
        (self.downloads / 'report.pdf').write_bytes(b'mine')
        self.assertEqual(self.press('Ctrl+S', mode), 'attachments')
        self.assertEqual(self.last(), 'Saved report.pdf as report (1).pdf in your Downloads folder, '
                                      'because a file with that name was already there.')
        self.assertEqual((self.downloads / 'report (1).pdf').read_bytes(), REPORT)
        self.assertEqual((self.downloads / 'report.pdf').read_bytes(), b'mine')
        self.press('Ctrl+Shift+S', mode)
        self.assertEqual(self.last(), 'Saved 3 attachments in your Downloads folder: report (2).pdf, photo.jpg, setup.exe.')
        self.assertEqual((self.downloads / 'photo.jpg').read_bytes(), PHOTO)
        leftovers = [p.name for p in self.downloads.iterdir() if p.name.startswith('.')]
        self.assertEqual(leftovers, [])

    def test_save_to_a_named_or_typed_folder(self):
        mode = self.press('L', self.inbox())
        target = self.root / 'Keep'; target.mkdir()
        app.handle('Save attachment to ' + str(target) + '.', mode)
        self.assertEqual(self.last(), 'Saved report.pdf in your Keep folder.')
        # The menu item asks for the folder; the typed answer is the folder.
        app.handle('save attachment to folder', mode)
        self.assertIn('Type or say a folder', self.last())
        self.assertTrue(app.app_context('attachments').get('task_input'))
        app.handle_keyboard(str(self.root / 'Nowhere'), mode)
        self.assertIn('That folder does not exist', self.last())
        app.handle_keyboard(str(target), mode)
        self.assertEqual(self.last(), 'Saved report.pdf as report (1).pdf in your Keep folder, because a file with that name was already there.')
        with patch('attachment_screen.place', return_value=target):
            app.handle('Save all attachments to Documents', mode)
        self.assertIn('Saved 3 attachments in your Keep folder', self.last())

    def test_save_from_the_inbox_without_the_list(self):
        mode = self.inbox()
        app.handle('save all attachments', mode)
        self.assertEqual(self.last(), 'Saved 3 attachments in your Downloads folder: report.pdf, photo.jpg, setup.exe.')
        app.handle('save attachment', mode)
        self.assertIn('Choose one and press Control+S', ' '.join(self.said[-2:]))

    def test_programs_ask_first_and_other_files_open(self):
        mode = self.press('L', self.inbox())
        self.press('End', mode)
        self.press('Enter', mode)
        self.assertEqual(self.last(), 'setup.exe is a program or script. Open it? Only open programs from people you trust. Say Yes or No.')
        self.assertEqual(app.app_context('attachments')['confirmation'], 'no')
        self.press('Enter', mode)        # No is chosen first
        self.assertEqual(self.started, [])
        self.assertIn('Not opened.', self.last())
        self.press('Enter', mode)
        self.press('Y', mode)
        self.assertEqual(len(self.started), 1)
        self.assertEqual(Path(self.started[0]).read_bytes(), PROGRAM)
        self.assertEqual(Path(self.started[0]).parent.name, 'Opened attachments')
        self.press('Up', mode)
        self.press('Enter', mode)
        self.assertEqual(Path(self.started[-1]).name, 'photo.jpg')
        self.assertIn('in its Windows app', self.last())

    def test_a_document_opens_in_the_reader_and_escape_comes_back(self):
        AttachmentMailbox.files['m3'] = [('2', 'notes.txt', b'Clinic opens at nine.')]
        self.addCleanup(AttachmentMailbox.files.pop, 'm3')
        mode = self.inbox()
        app.mail_session.rows.append({'id': 'm3', 'from': 'Al <al@example.com>', 'subject': 'Notes'})
        app.handle('read message 3', mode)
        mode = self.press('L', mode)
        mode = self.press('Enter', mode)
        self.assertEqual(mode, 'files')
        settle(app.FILE_MANAGER)
        mode = app.poll_file_manager(mode)
        self.assertEqual(mode, 'file_reader')
        self.assertIn('Clinic opens at nine.', self.last())
        self.assertEqual(self.press('Escape', mode), 'attachments')
        self.assertEqual(self.last(), 'Attachments, 1 file. notes.txt, 21 bytes, 1 of 1.')

    def test_forward_says_the_original_files_go_along(self):
        mode = self.inbox()
        app.handle('open current message', mode)
        self.assertEqual(app.handle('forward', mode), 'email_draft')
        self.assertIn('The original 3 attachments go with it: report.pdf, photo.jpg, setup.exe.', self.last())
        self.assertNotIn('not included', self.last())
        draft = app.email_draft
        # Take one off from the draft's own list.
        mode = app.handle('list attachments', 'email_draft')
        self.assertEqual(mode, 'attachments')
        self.press('Down', mode)
        self.press('Delete', mode)
        self.assertEqual(self.last(), 'Removed photo.jpg. 2 attachments left. setup.exe, 10 bytes, 2 of 2.')
        self.assertEqual(draft.response_context['dropped_attachments'], ['photo.jpg'])
        # A received message's files cannot be taken off it.
        self.press('Escape', mode)


class DraftAttachmentTests(FlowTestCase):
    def test_control_o_picks_a_file_and_comes_back_to_the_same_field(self):
        draft = self.draft(compose_step='subject')
        mode = self.press('Ctrl+O', 'email_draft')
        self.assertEqual(mode, 'files')
        self.assertTrue(self.last().startswith('Attach a file.'))
        self.settle_files(mode)
        app.FILE_MANAGER.choose('letter.txt')
        mode = self.press('Enter', mode)
        self.assertEqual(mode, 'email_draft')
        self.assertEqual(self.last(), 'Attached letter.txt. 1 attachment. Back to email draft. Subject field.')
        self.assertEqual(draft.compose_step, 'subject')
        self.assertEqual([a['name'] for a in draft.attachments], ['letter.txt'])
        self.assertIsNone(app.task_navigation().picker)
        # The saved draft has it too, and reopens with it.
        again = VoiceEmail.open_existing(self.root / 'data' / 'Email Drafts', 'Note')
        self.assertEqual(again.attachments[0]['path'], str(self.files / 'letter.txt'))

    def test_several_files_folders_and_escape(self):
        draft = self.draft()
        mode = self.settle_files(app.handle('attach a file', 'email_draft'))
        app.FILE_MANAGER.choose('Old letters')
        app.handle_keyboard('toggle selection', mode)
        mode = self.press('Enter', mode)          # a folder opens; nothing is attached
        self.settle_files(mode)
        self.assertEqual(mode, 'files')
        self.assertEqual(draft.attachments, [])
        app.handle_keyboard('escape', mode); self.settle_files(mode)
        app.FILE_MANAGER.choose('letter.txt'); app.handle_keyboard('toggle selection', mode)
        app.FILE_MANAGER.choose('photo.jpg'); app.handle_keyboard('toggle selection', mode)
        self.assertEqual(self.press('Enter', mode), 'email_draft')
        self.assertEqual(sorted(a['name'] for a in draft.attachments), ['letter.txt', 'photo.jpg'])
        self.assertIn('. 2 attachments. Back to email draft.', self.last())
        # Escape out of the drive list goes back to the draft with nothing attached.
        mode = self.settle_files(self.press('Ctrl+O', 'email_draft'))
        app.handle_keyboard('drive list', mode); self.settle_files(mode)
        self.assertEqual(self.press('Escape', mode), 'email_draft')
        self.assertEqual(self.last(), 'Nothing attached. Back to email draft.')
        self.assertEqual(len(draft.attachments), 2)

    def test_list_and_remove_by_key_and_by_name(self):
        draft = self.draft()
        draft.attach([self.files / 'letter.txt', self.files / 'photo.jpg'])
        mode = self.press('Ctrl+L', 'email_draft')
        self.assertEqual(mode, 'attachments')
        self.assertEqual(self.last(), 'Attachments, 2 files. letter.txt, 9 bytes, 1 of 2.')
        self.press('Delete', mode)
        self.assertEqual(self.last(), 'Removed letter.txt. 1 attachment left. photo.jpg, 14 bytes, 1 of 1.')
        self.assertEqual(self.press('Escape', mode), 'email_draft')
        self.assertEqual(app.handle('remove attachment photo', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), 'Removed photo.jpg. No attachments left.')
        self.assertEqual(self.press('Ctrl+L', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), 'This email has no attachments. Say attach a file, or press Control+O.')

    def test_writing_that_only_sounds_like_a_command_is_text(self):
        draft = self.draft()
        for sentence in ('Remove attachment if it is too big.', 'Attach a file to the report tomorrow.',
                         'Remove the attachment from last week.'):
            app.handle(sentence, 'email_draft')
        text = ' '.join(p.text for p in draft.paragraphs)
        self.assertIn('Remove attachment if it is too big.', text)
        self.assertIn('Attach a file to the report tomorrow.', text)
        self.assertIn('Remove the attachment from last week.', text)

    def test_send_review_names_files_and_a_missing_file_keeps_the_draft(self):
        draft = self.draft()
        draft.attach([self.files / 'letter.txt', self.files / 'photo.jpg'])
        with patch('companion.ProtectedStore') as store:
            store.return_value.load.return_value = {'address': 'me@yahoo.com'}
            store.return_value.selected.return_value = ('yahoo', 'me@yahoo.com')
            app.handle('send it', 'email_draft')
            self.assertIn('Subject Hi. With 2 attachments: letter.txt, photo.jpg. Message: See the files.', self.last())
            self.assertIsNotNone(app.pending_send)
            app.pending_send = None
            (self.files / 'photo.jpg').unlink()
            app.handle('send it', 'email_draft')
        self.assertEqual(self.last(), 'photo.jpg is no longer in its folder, so this email was not sent. Put the file back, '
                                      'or remove it from the email with Control+L and Delete. The draft is saved.')
        self.assertIsNone(app.pending_send)
        self.assertTrue(draft.path.exists())


class FileManagerEmailTests(FlowTestCase):
    def files_screen(self):
        mode = app.handle('file manager', 'awake')
        return self.settle_files(mode)

    def test_control_n_emails_the_chosen_files(self):
        mode = self.files_screen()
        app.FILE_MANAGER.choose('letter.txt'); app.handle_keyboard('toggle selection', mode)
        app.FILE_MANAGER.choose('photo.jpg'); app.handle_keyboard('toggle selection', mode)
        self.assertEqual(self.press('Ctrl+N', mode), 'email_draft')
        self.assertEqual(self.last(), 'New email. Attached letter.txt and photo.jpg. 2 attachments. To field. '
                                      'Who do you want to send it to? Type an address, or say a saved contact name.')
        self.assertEqual(app.email_draft.compose_step, 'recipient')
        self.assertTrue(app.email_draft.path.exists())

    def test_voice_and_menu_do_the_same_and_folders_are_refused(self):
        mode = self.files_screen()
        app.FILE_MANAGER.choose('Old letters')
        self.assertEqual(app.handle('Email this file.', mode), 'files')
        self.assertEqual(self.last(), 'Old letters is a folder. Choose files, not folders.')
        app.FILE_MANAGER.choose('letter.txt')
        self.press('Alt', mode)
        index = next(i for i, item in enumerate(app.FILE_MANAGER.menu_items()) if item[2] == 'email this file')
        app.FILE_MANAGER.menu_index = index
        self.assertEqual(self.press('Enter', mode), 'email_draft')
        self.assertEqual([a['name'] for a in app.email_draft.attachments], ['letter.txt'])

    def test_control_o_adds_to_the_open_draft(self):
        mode = self.files_screen()
        app.FILE_MANAGER.choose('photo.jpg')
        self.assertEqual(self.press('Ctrl+O', mode), 'files')
        self.assertEqual(self.last(), 'No email draft is open. Press Control+N to start a new email with this file.')
        draft = self.draft()
        self.assertEqual(self.press('Ctrl+O', mode), 'email_draft')
        self.assertEqual(self.last(), 'Attached photo.jpg. 1 attachment. Back to email draft.')
        self.assertEqual([a['name'] for a in draft.attachments], ['photo.jpg'])

    def test_old_control_t_still_attaches_and_names_the_new_key_once(self):
        # Will, October 2026: Control+O attaches. Habits from Control+T keep working;
        # the first use in a session says the new key, later uses stay quiet.
        attachment_screen.OLD_KEY_NOTICE_GIVEN = False
        self.addCleanup(setattr, attachment_screen, 'OLD_KEY_NOTICE_GIVEN', False)
        draft = self.draft()
        mode = self.press('Ctrl+T', 'email_draft')
        self.assertEqual(mode, 'files')
        self.assertTrue(self.last().startswith('Control+T is now Control+O. Attach a file.'), self.last())
        self.settle_files(mode)
        app.FILE_MANAGER.choose('letter.txt')
        self.assertEqual(self.press('Enter', mode), 'email_draft')
        mode = self.press('Ctrl+T', 'email_draft')
        self.assertTrue(self.last().startswith('Attach a file.'), self.last())
        self.settle_files(mode)
        app.FILE_MANAGER.choose('photo.jpg')
        self.assertEqual(self.press('Enter', mode), 'email_draft')
        self.assertEqual(sorted(a['name'] for a in draft.attachments), ['letter.txt', 'photo.jpg'])
        # File manager's Control+T still works there too, without the notice now.
        mode = self.files_screen()
        app.FILE_MANAGER.choose('letter.txt')
        self.assertEqual(self.press('Ctrl+T', mode), 'files')
        self.assertEqual(self.last(), 'letter.txt is already attached.')

    def test_old_control_t_in_file_manager_names_the_new_key(self):
        attachment_screen.OLD_KEY_NOTICE_GIVEN = False
        self.addCleanup(setattr, attachment_screen, 'OLD_KEY_NOTICE_GIVEN', False)
        mode = self.files_screen()
        app.FILE_MANAGER.choose('photo.jpg')
        draft = self.draft()
        self.assertEqual(self.press('Ctrl+T', mode), 'email_draft')
        self.assertEqual(self.last(), 'Control+T is now Control+O. Attached photo.jpg. 1 attachment. Back to email draft.')
        self.assertEqual([a['name'] for a in draft.attachments], ['photo.jpg'])


def _thunderbird_case():
    from test_thunderbird_mail import ThunderbirdEndToEndTests
    return ThunderbirdEndToEndTests


@unittest.skipUnless(shutil.which('node'), 'Node.js runs the simulated Thunderbird')
class ThunderbirdAttachmentTests(unittest.TestCase):
    """The real add-on in Node, the real host code and relay, real bytes both ways."""
    setUp = _thunderbird_case().setUp
    tearDown = _thunderbird_case().tearDown
    finish = _thunderbird_case().finish

    def account(self):
        import mail_accounts
        mail_accounts.connect('thunderbird', self.folder)
        return mail_accounts.account_token('thunderbird', self.folder)

    def test_list_save_and_send_through_thunderbird(self):
        import mail_attachments
        from email_delivery import submit
        from thunderbird_mailbox import ThunderbirdMailbox
        address, account = self.account()
        mailbox = ThunderbirdMailbox(address, account)
        body = mailbox.read_message('24')
        self.assertEqual([a['name'] for a in body.attachments], ['report.pdf', 'photo.jpg'])
        report = body.attachments[0]
        saved = mail_attachments.save_stream(self.folder, report['name'], mailbox.attachment_chunks('24', report))
        self.assertEqual(saved.read_bytes(), b'%PDF-1.4 report ' * 90000)
        # A new message with a file bigger than one piece.
        big = self.folder / 'scan.pdf'; big.write_bytes(os.urandom(1300 * 1024))
        draft = VoiceEmail(self.folder / 'Email Drafts', recipient='sam@example.com', subject='Scan', provider='thunderbird')
        draft.paragraphs = [Paragraph('The scan.')]
        draft.attach([big])
        submit('thunderbird', draft, account, sender=address)
        # A forward keeps the original's files, less the one taken off.
        context = mailbox.response_context('24', 'forward', address)
        self.assertEqual([a['name'] for a in context['original_attachments']], ['report.pdf', 'photo.jpg'])
        forward = VoiceEmail(self.folder / 'Email Drafts', recipient='x@example.com', subject=context['subject'],
                             provider='thunderbird', response_context=dict(context, dropped_attachments=['photo.jpg']))
        forward.paragraphs = [Paragraph('FYI')]
        submit('thunderbird', forward, account, sender=address)
        sent = self.finish()['sent']
        self.assertEqual(sent[0]['attachments'], [{'name': 'scan.pdf', 'size': 1300 * 1024}])
        self.assertEqual(sent[1]['kind'], 'forward')
        self.assertNotIn('photo.jpg', [a['name'] for a in sent[1]['attachments']])
        self.assertIn('report.pdf', [a['name'] for a in sent[1]['attachments']])

    def test_a_file_gone_before_sending_is_never_sent(self):
        from email_delivery import DeliveryError, submit
        address, account = self.account()
        gone = self.folder / 'gone.txt'; gone.write_text('x')
        draft = VoiceEmail(self.folder / 'Email Drafts', recipient='sam@example.com', subject='Gone', provider='thunderbird')
        draft.paragraphs = [Paragraph('Hi')]
        draft.attach([gone])
        gone.unlink()
        with self.assertRaises(DeliveryError) as raised:
            submit('thunderbird', draft, account, sender=address)
        self.assertFalse(raised.exception.uncertain)
        self.assertIn('gone.txt is no longer in its folder', str(raised.exception))
        self.assertEqual(self.finish()['sent'], [])


class OlderAddOnTests(unittest.TestCase):
    def test_an_older_thunderbird_connection_says_to_update(self):
        import thunderbird_mailbox
        from email_delivery import DeliveryError
        from mailbox_access import MailboxError
        with patch.object(thunderbird_mailbox, 'call', side_effect=MailboxError(thunderbird_mailbox.UNSUPPORTED)):
            with self.assertRaises(MailboxError) as raised:
                thunderbird_mailbox.ThunderbirdMailbox('me@example.com', {}).attachments('5')
        self.assertEqual(str(raised.exception), thunderbird_mailbox.NEEDS_NEWER_ADDON)
        from thunderbird_bridge import ThunderbirdError
        bridge = mock.Mock()
        bridge.request.side_effect = ThunderbirdError(thunderbird_mailbox.UNSUPPORTED)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.txt'; path.write_text('x')
            with self.assertRaises(DeliveryError) as raised:
                thunderbird_mailbox.upload_files(bridge, [{'path': str(path), 'name': 'a.txt', 'size': 1}])
        self.assertEqual(str(raised.exception), thunderbird_mailbox.NEEDS_NEWER_ADDON)
        self.assertFalse(raised.exception.uncertain)


if __name__ == '__main__':
    unittest.main()

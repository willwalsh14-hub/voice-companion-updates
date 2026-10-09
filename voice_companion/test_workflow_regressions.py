import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import companion
from app_window import CompanionWindow
from dictation_text import clean_dictation
from email_draft import VoiceEmail
from email_delivery import microsoft_request, gmail_request
from document_editor import Paragraph
from mail_voice import MailSession

class WorkflowRegressions(unittest.TestCase):
    def test_sleep_preserves_email_folder_selection_and_draft(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(companion, 'speak'), patch.object(companion, 'MEDIA_HUB', None):
            session = MailSession('gmail', folder)
            session.folder_name, session.folder_id = 'Drafts', 'DRAFT'
            session.rows = [{'id': 'one'}]
            session.current = 1
            with patch.object(companion, 'mail_session', session):
                self.assertEqual(companion.handle('go to sleep', 'mailbox'), 'sleep')
                self.assertIs(companion.mail_session, session)
                self.assertEqual(companion.resume_from_sleep(), 'mailbox')
                self.assertEqual((session.folder_id, session.current), ('DRAFT', 1))
            draft = VoiceEmail(Path(folder), recipient='original@example.com', compose_step='recipient')
            with patch.object(companion, 'email_draft', draft):
                self.assertEqual(companion.handle('go to sleep', 'email_draft'), 'sleep')
                self.assertEqual(companion.resume_from_sleep(), 'email_draft')
                self.assertIs(companion.email_draft, draft)
                self.assertEqual(draft.compose_step, 'recipient')

    def test_gmail_drafts_alias_loads_actual_system_label(self):
        client = Mock()
        client.folders.return_value = [('INBOX', 'INBOX'), ('DRAFT', 'DRAFT')]
        client.list_messages.return_value = ([], None)
        with tempfile.TemporaryDirectory() as folder:
            session = MailSession('gmail', folder)
            with patch.object(session, 'client', return_value=client):
                self.assertIn('No messages', session.process('go to drafts'))
            client.list_messages.assert_called_once_with('DRAFT', None, limit=10)

    def test_lowercase_spoken_punctuation_in_reported_sentence(self):
        self.assertEqual(clean_dictation('Just testing with the app period still bugs that need fixing comma functionality was added and now is apparently broken.'),
                         'Just testing with the app. still bugs that need fixing, functionality was added and now is apparently broken.')
        self.assertEqual(clean_dictation('hello, comma friend. period this works question mark'), 'hello, friend. this works?')

    def test_keyboard_to_entry_submit_and_echo_for_all_draft_types(self):
        for action in ('', 'forward', 'reply', 'reply_all'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                draft = VoiceEmail(Path(folder), recipient='old@example.com', subject='Subject', compose_step='body')
                if action: draft.response_context = {'action': action, 'source_id':'source'}
                window = CompanionWindow('test')
                with patch.object(companion, 'email_draft', draft), patch.object(companion, 'APP_WINDOW', window), patch.object(companion, 'INPUT_MODE', 'mixed'), patch.object(companion, 'speak'):
                    companion.handle('go to to', 'email_draft')
                    field, value = window.ui_actions.get_nowait()[1]
                    self.assertEqual((field, value), ('recipient', 'old@example.com'))
                    window.email_field = field
                    companion.focus_email_entry()
                    self.assertEqual(window.ui_actions.get_nowait(), ('email_focus', None), 'Repeated prompts must restore focus without replacing typed text')
                    window.report_entry_key('new', 'new@', 3, 4, 'at')
                    self.assertEqual(window.key_feedback.get_nowait(), 'at sign')
                    self.assertTrue(window.submit_entry('new@example.com'))
                    companion.handle(window.commands.get_nowait(), 'email_draft', typed=True)
                    self.assertEqual(draft.recipient, 'new@example.com')
                    self.assertEqual(draft.compose_step, 'body' if action else 'subject')

    def test_edited_reply_recipient_is_used_for_outlook_and_gmail(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='old@example.com', subject='Re: Test', provider='outlook')
            draft.response_context = {'action':'reply', 'source_id':'source', 'thread_id':'thread'}
            draft.process('go to to');draft.process('new@example.com')
            draft.paragraphs = [Paragraph(text='Reply body')]
            request = microsoft_request(draft, 'token')
            self.assertEqual(json.loads(request.data)['message']['toRecipients'][0]['emailAddress']['address'], 'new@example.com')
            import base64
            from email.parser import BytesParser
            raw = json.loads(gmail_request(draft, 'token').data)['raw']
            self.assertEqual(BytesParser().parsebytes(base64.urlsafe_b64decode(raw))['To'], 'new@example.com')

    def test_send_confirmation_completes_before_current_folder_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='friend@example.com', subject='Test', provider='gmail')
            draft.paragraphs = [Paragraph(text='Message')]
            session = MailSession('gmail', folder)
            session.folder_name, session.folder_id, session.page_cursor = 'Projects', 'projects', 'page2'
            events = []
            def refresh(cursor):
                events.append(('refresh', cursor, session.folder_id));return 'Folder reloaded.'
            with patch.object(companion, 'email_draft', draft), patch.object(companion, 'mail_session', session), patch.object(companion, 'pending_send', (draft.send_hash(), 'gmail', 'me@example.com')), patch.object(companion, 'account_token', return_value=('me@example.com','token')), patch.object(companion, 'submit', side_effect=lambda *a, **k:events.append('send')), patch.object(companion, 'speak', side_effect=lambda text:events.append(text)), patch.object(companion, 'finish_mail_announcement', side_effect=lambda:events.append('announcement finished')), patch.object(session, 'list_messages', side_effect=refresh):
                self.assertEqual(companion.handle('yes', 'email_draft'), 'mailbox')
            self.assertEqual(events[:4], ['send','Email sent successfully.','announcement finished',('refresh','page2','projects')])

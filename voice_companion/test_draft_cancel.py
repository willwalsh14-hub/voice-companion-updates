import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import companion
from email_draft import VoiceEmail
from email_delivery import microsoft_request
from document_editor import Paragraph

class DraftCancelTests(unittest.TestCase):
    def test_cancel_removes_new_autosaves_from_every_field_and_draft_type(self):
        for action in ('', 'reply', 'reply_all', 'forward'):
            for field in ('recipient','cc','bcc','subject','body'):
                with self.subTest(action=action,field=field), tempfile.TemporaryDirectory() as folder:
                    draft=VoiceEmail(Path(folder),recipient='old@example.com',subject='Test',provider='gmail')
                    if action:draft.response_context={'action':action,'source_id':'original'}
                    draft.compose_step=field
                    draft.save();draft.save()
                    unrelated=Path(folder)/'unrelated.txt';unrelated.write_text('keep')
                    inbox=Mock();inbox.process.return_value='Inbox opened.'
                    with patch.object(companion,'email_draft',draft),patch.object(companion,'mail_session',inbox),patch.object(companion,'speak'),patch.object(companion,'finish_mail_announcement'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'INPUT_MODE','dictation'):
                        self.assertEqual(companion.handle('cancel','email_draft'),'mailbox')
                        self.assertIsNone(companion.email_draft)
                    self.assertEqual(list(Path(folder).iterdir()),[unrelated])
                    inbox.process.assert_called_once_with('open inbox')

    def test_cancel_aliases_preempt_recipient_and_clear_pending_send(self):
        for phrase in ('Cancel this draft.','discard email','cancel email','back to inbox',
                       'go to inbox','return to my inbox','go back to the inbox','take me to inbox','back','go back'):
            with self.subTest(phrase=phrase),tempfile.TemporaryDirectory() as folder:
                draft=VoiceEmail(Path(folder),compose_step='recipient')
                with patch.object(companion,'email_draft',draft),patch.object(companion,'mail_session',Mock()),patch.object(companion,'pending_send',('hash','gmail','me')),patch.object(companion,'speak'),patch.object(companion,'finish_mail_announcement'),patch.object(companion,'APP_WINDOW',None):
                    companion.handle(phrase,'email_draft')
                    self.assertIsNone(companion.pending_send)
                    self.assertEqual(draft.recipient,'')

    def test_cancel_restores_preexisting_draft_and_recovery_files(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),recipient='original@example.com',subject='Original')
            draft.save();draft.save()
            baseline={p.name:p.read_bytes() for p in Path(folder).iterdir()}
            reopened=VoiceEmail.open_existing(Path(folder),draft.title)
            reopened.process('email to changed@example.com')
            reopened.process('subject is Changed')
            reopened.discard()
            self.assertEqual({p.name:p.read_bytes() for p in Path(folder).iterdir()},baseline)

    def test_cancel_does_not_remove_send_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),recipient='friend@example.com')
            draft.last_attempt_hash='attempt';draft.save()
            self.assertFalse(draft.discard())
            self.assertTrue(draft.state_path.exists())

    def test_cc_bcc_are_editable_in_every_response_and_outgoing_request(self):
        for action in ('reply','reply_all','forward'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as folder:
                draft=VoiceEmail(Path(folder),recipient='friend@example.com',subject='Subject',provider='outlook')
                draft.response_context={'action':action,'source_id':'original'}
                draft.paragraphs=[Paragraph(text='Reply body')]
                for field in ('cc','bcc'):
                    self.assertIn(field.upper(),draft.process('go to '+field))
                    draft.process(field+'@example.com')
                    self.assertEqual(getattr(draft,field),[field+'@example.com'])
                payload=json.loads(microsoft_request(draft,'token').data)
                self.assertEqual(payload['message']['bccRecipients'][0]['emailAddress']['address'],'bcc@example.com')
                self.assertNotIn('toRecipients',payload)

    def test_quick_test_explicitly_opens_entry_window_and_reports_startup_errors(self):
        root=Path(__file__).parent
        self.assertIn('& $python companion.py --window', (root/'quick-test.ps1').read_text())
        source=(root/'companion.py').read_text()
        self.assertIn("windowed = sys.stdout is None or '--window' in sys.argv",source)
        self.assertIn("'window-error.txt'",source)

    def test_character_echo_uses_local_speech_even_when_ai_voice_is_enabled(self):
        with patch.object(companion,'TEXT_MODE',False),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'update_audio_ducking'),patch.object(companion,'voice',create=True) as voice,patch.object(companion,'AI_SPEECH') as ai:
            companion.speak_keyboard_feedback('at sign')
            voice.Speak.assert_called_once_with('at sign',3)
            ai.speak.assert_not_called()

    def test_explicit_save_is_kept_when_later_edits_are_canceled(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),recipient='saved@example.com',compose_step='cc')
            self.assertIn('saved locally',draft.process('save draft'))
            baseline={p.name:p.read_bytes() for p in Path(folder).iterdir()}
            draft.process('go to to');draft.process('changed@example.com')
            draft.discard()
            self.assertEqual({p.name:p.read_bytes() for p in Path(folder).iterdir()},baseline)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import companion
from email_draft import VoiceEmail
from test_email_delivery import yahoo_message
from document_editor import Paragraph

class LeaveQuestionTests(unittest.TestCase):
    """Escape, Cancel, Back or Go back ask before a written email is thrown away (Will, October 2026)."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.draft = VoiceEmail(Path(self.folder.name), recipient='sam@example.com', subject='Lunch',
                                provider='thunderbird', compose_step='body')
        self.draft.process('See you at noon.')
        self.inbox = Mock(); self.inbox.process.return_value = 'Inbox opened.'
        self.said = []
        for item in (patch.object(companion, 'email_draft', self.draft), patch.object(companion, 'mail_session', self.inbox),
                     patch.object(companion, 'speak', side_effect=lambda text, *a, **k: self.said.append(text)),
                     patch.object(companion, 'finish_mail_announcement'), patch.object(companion, 'APP_WINDOW', None),
                     patch.object(companion, 'pending_send', None), patch.object(companion, 'focus_email_entry'),
                     patch.object(companion, 'sync_app_context')):
            item.start(); self.addCleanup(item.stop)

    def saved_text(self):
        return VoiceEmail.open_existing(Path(self.folder.name), self.draft.title).paragraphs[0].text

    def test_every_leaving_word_and_escape_asks_with_save_chosen_first(self):
        for phrase in ('Cancel.', 'back', 'Go back.', 'discard draft', 'back to inbox'):
            with self.subTest(phrase=phrase):
                self.said.clear()
                self.assertEqual(companion.handle(phrase, 'email_draft'), 'draft_leave')
                self.assertIn('Save it as a draft, throw it away, or keep writing', self.said[-1])
                self.assertEqual(companion.app_context('draft_leave')['confirmation'], 'save')
                self.assertEqual(companion.handle('keep writing', 'draft_leave'), 'email_draft')
        self.assertEqual(companion.navigation_key('Escape', 'email_draft'), 'cancel')

    def test_no_and_escape_keep_writing_and_lose_nothing(self):
        for answer in ('No.', 'Cancel', companion.navigation_key('Escape', 'draft_leave')):
            with self.subTest(answer=answer):
                companion.handle('cancel', 'email_draft')
                self.assertEqual(companion.handle(answer, 'draft_leave'), 'email_draft')
                self.assertIs(companion.email_draft, self.draft)
                self.assertTrue(self.said[-1].startswith('Keep writing.'))
                self.assertEqual(self.saved_text(), 'See you at noon.')

    def test_enter_on_the_first_choice_saves_the_draft(self):
        companion.handle('go back', 'email_draft')
        self.assertEqual(companion.handle(companion.navigation_key('Enter', 'draft_leave'), 'draft_leave'), 'mailbox')
        self.assertIsNone(companion.email_draft)
        self.assertIn('saved locally', ' '.join(self.said))
        self.assertEqual(self.saved_text(), 'See you at noon.')
        self.inbox.process.assert_called_once_with('open inbox')

    def test_arrows_choose_and_stop_at_the_ends(self):
        companion.handle('cancel', 'email_draft')
        self.assertIsNone(companion.navigation_key('Up', 'draft_leave'))
        self.assertEqual(self.said[-1], 'Start of list.')
        companion.navigation_key('Down', 'draft_leave')
        self.assertEqual(self.said[-1], 'Throw it away, 2 of 3.')
        self.assertEqual(companion.navigation_key('Enter', 'draft_leave'), 'throw it away')
        companion.navigation_key('End', 'draft_leave')
        self.assertEqual(self.said[-1], 'Keep writing, 3 of 3.')
        companion.navigation_key('Down', 'draft_leave')
        self.assertEqual(self.said[-1], 'End of list.')
        self.assertEqual(companion.handle('that one', 'draft_leave'), 'email_draft')

    def test_throw_it_away_removes_the_unsaved_draft(self):
        companion.handle('cancel', 'email_draft')
        self.assertEqual(companion.handle('throw it away', 'draft_leave'), 'mailbox')
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])
        self.assertIn('Email thrown away.', self.said)

    def test_other_words_repeat_the_question(self):
        companion.handle('cancel', 'email_draft')
        self.assertEqual(companion.handle('Hello there.', 'draft_leave'), 'draft_leave')
        self.assertIn('throw it away, or keep writing', self.said[-1])
        self.assertIs(companion.email_draft, self.draft)

    def test_an_empty_draft_closes_without_asking(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = VoiceEmail(Path(folder), compose_step='recipient')
            with patch.object(companion, 'email_draft', empty):
                self.assertEqual(companion.handle('cancel', 'email_draft'), 'mailbox')
            reply = VoiceEmail(Path(folder), recipient='sam@example.com', subject='Re: Lunch', compose_step='body')
            reply.response_context = {'action': 'reply', 'source_id': '1'}
            reply.save()
            with patch.object(companion, 'email_draft', reply):
                self.assertEqual(companion.handle('go back', 'email_draft'), 'mailbox')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_a_draft_saved_and_not_changed_closes_and_stays_saved(self):
        self.draft.process('save draft')
        self.assertEqual(companion.handle('cancel', 'email_draft'), 'mailbox')
        self.assertEqual(self.saved_text(), 'See you at noon.')


class DraftCancelTests(unittest.TestCase):
    def test_cancel_removes_new_autosaves_from_every_field_and_draft_type(self):
        for action in ('', 'reply', 'reply_all', 'forward'):
            for field in ('recipient','cc','bcc','subject','body'):
                with self.subTest(action=action,field=field), tempfile.TemporaryDirectory() as folder:
                    draft=VoiceEmail(Path(folder),recipient='old@example.com',subject='Test',provider='thunderbird')
                    if action:draft.response_context={'action':action,'source_id':'original'}
                    draft.compose_step=field
                    draft.save();draft.save()
                    unrelated=Path(folder)/'unrelated.txt';unrelated.write_text('keep')
                    inbox=Mock();inbox.process.return_value='Inbox opened.'
                    with patch.object(companion,'email_draft',draft),patch.object(companion,'mail_session',inbox),patch.object(companion,'speak'),patch.object(companion,'finish_mail_announcement'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'INPUT_MODE','dictation'):
                        # Will changed the design (October 2026): a written draft asks first.
                        # A reply with nothing typed closes at once; an address typed for a new email or forward asks.
                        result=companion.handle('cancel','email_draft')
                        self.assertEqual(result,'draft_leave' if action in ('','forward') else 'mailbox')
                        if result=='draft_leave':self.assertEqual(companion.handle('Throw it away.','draft_leave'),'mailbox')
                        self.assertIsNone(companion.email_draft)
                    self.assertEqual(list(Path(folder).iterdir()),[unrelated])
                    inbox.process.assert_called_once_with('open inbox')

    def test_cancel_aliases_preempt_recipient_and_clear_pending_send(self):
        for phrase in ('Cancel this draft.','discard email','cancel email','back to inbox',
                       'go to inbox','return to my inbox','go back to the inbox','take me to inbox','back','go back'):
            with self.subTest(phrase=phrase),tempfile.TemporaryDirectory() as folder:
                draft=VoiceEmail(Path(folder),compose_step='recipient')
                with patch.object(companion,'email_draft',draft),patch.object(companion,'mail_session',Mock()),patch.object(companion,'pending_send',('hash','thunderbird','me')),patch.object(companion,'speak'),patch.object(companion,'finish_mail_announcement'),patch.object(companion,'APP_WINDOW',None):
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
                draft=VoiceEmail(Path(folder),recipient='friend@example.com',subject='Subject',provider='yahoo')
                draft.response_context={'action':action,'source_id':'original'}
                draft.paragraphs=[Paragraph(text='Reply body')]
                for field in ('cc','bcc'):
                    self.assertIn(field.upper(),draft.process('go to '+field))
                    draft.process(field+'@example.com')
                    self.assertEqual(getattr(draft,field),[field+'@example.com'])
                sent=yahoo_message(draft)
                self.assertEqual((sent['Cc'],sent['Bcc'],sent['To']),('cc@example.com','bcc@example.com','friend@example.com'))

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

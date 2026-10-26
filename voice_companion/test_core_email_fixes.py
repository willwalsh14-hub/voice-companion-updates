"""Regression checks for app-wide and email bugs found in the October 2026 review."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import companion
import mail_accounts
from dictation_text import clean_dictation
from document_editor import Paragraph, VoiceDocument
from email_delivery import outgoing_body
from email_draft import VoiceEmail, normalize_recipient
from keyboard_text import paragraph_destination
from mailbox_access import message_date


class CompanionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.said = []
        for name, value in dict(TEXT_MODE=True, APP=self.folder, APP_WINDOW=None, MEDIA_HUB=None, INPUT_MODE='mixed',
                                document=None, email_draft=None, mail_session=None, pending_send=None,
                                help_session=None, SLEEP_RETURN_MODE='awake').items():
            p = patch.object(companion, name, value, create=True); p.start(); self.addCleanup(p.stop)
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            p = patch.object(companion, name, lambda text, *a, **k: self.said.append(str(text))); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'save_speech_settings'); p.start(); self.addCleanup(p.stop)

    def open_document(self):
        companion.document = VoiceDocument(self.folder / 'Docs'); companion.document.dictating = True
        companion.checkpoint_document()
        return companion.document

    def draft(self, **fields):
        draft = VoiceEmail(self.folder / 'Email Drafts', **{'recipient': 'friend@example.com', 'subject': 'Hi',
                                                            'provider': 'thunderbird', 'compose_step': 'body', **fields})
        draft.paragraphs = [Paragraph('See you soon.')]
        companion.email_draft = draft
        return draft


class DictationIsNotTakenAsCommandsTests(CompanionTestCase):
    SENTENCES = ('Rate this product five stars.', 'Pitch the tent by the lake.', 'Help with the dishes tonight',
                 'Capital letters are important.', 'Browser tabs are confusing.', 'Use voice commands to send mail',
                 'Use the back door at night.', 'Switch to the left lane at the light.',
                 'Volume two of the series is out.', 'Speed limits matter.')

    def test_sentences_are_written_in_dictation_and_normal_modes(self):
        for input_mode in ('dictation', 'mixed'):
            for sentence in self.SENTENCES:
                companion.INPUT_MODE = input_mode
                document = self.open_document()
                self.assertEqual(companion.handle(sentence, 'document'), 'document', sentence)
                self.assertEqual(companion.document_text(document), sentence, (input_mode, sentence))

    def test_real_commands_still_work_while_writing(self):
        self.open_document()
        with patch.object(companion, 'SPEECH_RATE', 0):
            companion.handle('rate 3', 'document')
            self.assertEqual(companion.SPEECH_RATE, 3)
        self.assertEqual(companion.handle('help with documents', 'document'), 'help')
        companion.help_session = None
        self.open_document()
        companion.handle('rate 11', 'document')
        self.assertIn('minus ten to ten', self.said[-1])
        self.assertEqual(companion.document_text(companion.document), '')

    def test_commands_only_mode_still_reports_bad_values(self):
        companion.INPUT_MODE = 'commands'
        self.open_document()
        companion.handle('volume banana', 'document')
        self.assertIn('Speech volume must be', self.said[-1])

    def test_body_sentences_starting_like_contact_commands_are_kept(self):
        draft = self.draft()
        for sentence in ('Update contact details as soon as you can.', 'Use the side door at noon.'):
            companion.handle(sentence, 'email_draft')
        self.assertIn('Update contact details as soon as you can.', companion.document_text(draft))
        self.assertIn('Use the side door at noon.', companion.document_text(draft))


class SendReviewTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        store = Mock(); store.load.return_value = {'address': 'me@gmail.com'}
        store.selected.return_value = ('thunderbird', 'me@gmail.com'); store.accounts.return_value = [('thunderbird', 'me@gmail.com')]
        for target, value in (('ProtectedStore', Mock(return_value=store)),
                              ('account_token', Mock(return_value=('me@gmail.com', 'tok'))),
                              ('MailSession', Mock()), ('finish_mail_announcement', Mock())):
            p = patch.object(companion, target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'submit'); self.submit = p.start(); self.addCleanup(p.stop)

    def test_spoken_yes_sends_in_dictation_only_mode(self):
        companion.INPUT_MODE = 'dictation'
        draft = self.draft()
        companion.handle('send email', 'email_draft', typed=True)
        companion.handle('yes', 'email_draft')
        self.assertTrue(self.submit.called)
        self.assertEqual(companion.document_text(draft), 'See you soon.')

    def test_yes_with_recognizer_punctuation_sends(self):
        draft = self.draft()
        companion.pending_send = (draft.send_hash(), 'thunderbird', 'me@gmail.com')
        companion.handle('Yes, please.', 'email_draft')
        self.assertTrue(self.submit.called)
        self.assertEqual(companion.document_text(draft), 'See you soon.')

    def test_a_sign_in_needed_to_send_opens_the_page_and_keeps_the_draft(self):
        # Will, 0.2.162: Office 365 needed a sign-in to send through Thunderbird.
        from email_delivery import DeliveryError
        draft = self.draft()
        error = DeliveryError('Microsoft wants you to sign in before Thunderbird can send this message.')
        error.sign_in_url = 'https://login.microsoftonline.com/x'
        self.submit.side_effect = error
        opened = []
        with patch.object(companion, 'open_thunderbird_sign_in', lambda mode, announce=True: opened.append((companion.THUNDERBIRD_SIGN_IN, announce)) or 'web'), \
             patch.object(companion, 'THUNDERBIRD_SIGN_IN', None), patch.object(companion, 'speak_status', self.said.append):
            companion.handle('send email', 'email_draft', typed=True)
            mode = companion.handle('yes', 'email_draft', typed=True)
            self.assertEqual(mode, 'web')
            self.assertEqual(opened, [('https://login.microsoftonline.com/x', False)])
        self.assertIn('Microsoft wants you to sign in', ' '.join(self.said))
        self.assertIn('The draft is saved locally.', ' '.join(self.said))
        self.assertEqual(companion.document_text(draft), 'See you soon.')

    def test_failed_local_save_before_sending_allows_a_retry(self):
        self.draft()
        companion.handle('send email', 'email_draft', typed=True)
        with patch.object(VoiceEmail, 'save', side_effect=OSError('disk full')):
            companion.handle('yes', 'email_draft', typed=True)
        self.assertFalse(self.submit.called)
        self.assertIn('was not sent', self.said[-1])
        companion.handle('send email', 'email_draft', typed=True)
        self.assertIn('Review before sending', self.said[-1])


class AppStateTests(CompanionTestCase):
    def test_typed_sleep_while_asleep_still_wakes_to_work(self):
        mode = companion.handle('go to sleep', 'awake')
        mode = companion.handle_keyboard('go to sleep', mode)
        self.assertEqual((mode, companion.SLEEP_RETURN_MODE), ('sleep', 'awake'))

    def test_locked_file_while_saving_typed_edits_does_not_stop_the_app(self):
        document = self.open_document()
        companion.KEYBOARD_DIRTY[id(document)] = (document, time.monotonic())
        self.addCleanup(companion.KEYBOARD_DIRTY.clear)
        with patch.object(document, 'save', side_effect=PermissionError('locked')):
            self.assertEqual(companion.handle('read document', 'document'), 'document')
        self.assertTrue(any('could not be saved yet' in text for text in self.said))

    def test_closed_mailbox_returns_to_the_main_menu(self):
        self.assertEqual(companion.handle('next', 'mailbox'), 'awake')

    def test_inbox_go_to_app_commands_are_not_folders(self):
        session = Mock(view='folder', pending=None, folder_picker=None, folder_choice=None)
        companion.mail_session = session
        self.assertEqual(companion.handle('go to main menu', 'mailbox'), 'awake')
        session.process.assert_not_called()
        companion.mail_session = session
        companion.handle('go to inbox', 'mailbox')
        session.process.assert_called_with('go to inbox')


class SettingsSnapshotTests(CompanionTestCase):
    def test_options_save_with_several_accounts_and_none_selected(self):
        import app_settings

        class Store:
            def __init__(self, folder): pass
            def accounts(self): return [('thunderbird', 'a@example.com'), ('yahoo', 'b@yahoo.com')]
            def selected(self): return None

        preferences = dict(companion.PREFERENCES)
        self.addCleanup(lambda: (companion.PREFERENCES.clear(), companion.PREFERENCES.update(preferences)))
        with patch.object(companion, 'ProtectedStore', Store), patch('windows_actions.startup_enabled', return_value=False), \
                patch.object(companion, 'save_preferences'), patch.object(companion, 'media'):
            values, context = app_settings.snapshot(companion)
            self.assertEqual(values['email_account'], '')
            values['email_format'] = 'plain text'
            app_settings.apply_settings(companion, values)
            self.assertEqual(companion.PREFERENCES['email_format'], 'plain text')


class EmailAddressTests(unittest.TestCase):
    def test_spoken_addresses(self):
        cases = {'bob at example dot com.': 'bob@example.com', 'Bob at example dot com.': 'bob@example.com',
                 'john underscore smith at gmail dot com': 'john_smith@gmail.com',
                 'mary dash jones at yahoo dot com': 'mary-jones@yahoo.com',
                 'friend at example dot co dot uk': 'friend@example.co.uk', 'john smith at gmail dot com': 'johnsmith@gmail.com'}
        for spoken, expected in cases.items():
            self.assertEqual(normalize_recipient(spoken), expected, spoken)

    def test_words_after_the_domain_are_rejected(self):
        for spoken in ('bob at example dot com next week', 'friend@example.com\r\nBcc: x@y.com'):
            with self.assertRaises(ValueError):
                normalize_recipient(spoken)

    def test_body_sentences_that_start_like_header_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder) / 'Email Drafts', recipient='friend@example.com', subject='Hi', compose_step='body')
            for sentence in ('Send to the team at the office.', 'CC is fine with me.',
                             'Email to Bob at example dot com next week.'):
                draft.process(sentence)
            self.assertEqual(draft.recipient, 'friend@example.com')
            self.assertEqual(draft.cc, [])
            body = ' '.join(p.text for p in draft.paragraphs)
            for words in ('Send to the team at the office.', 'CC is fine with me.', 'next week'):
                self.assertIn(words, body)
            draft.process('cc bob at example dot com')
            self.assertEqual(draft.cc, ['bob@example.com'])


class ResponseFieldTests(unittest.TestCase):
    def test_recipient_typed_in_a_reply_field_is_used(self):
        context = {'action': 'reply', 'source_id': 'AAMk1', 'recipient': 'orig@example.com', 'cc': [],
                   'subject': 'Re: Hi', 'original_body': '', 'source_address': 'me@example.com'}
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='orig@example.com', subject='Re: Hi', provider='thunderbird',
                               response_context=dict(context), compose_step='recipient')
            draft.paragraphs = [Paragraph('Hello')]
            with patch.object(companion, 'email_draft', draft), patch.object(companion, 'speak'), \
                    patch.object(companion, 'navigation_key', lambda key, mode: None):
                companion.commit_email_field_tab({'field': 'recipient', 'value': 'other@example.com', 'backward': False}, 'email_draft')
            from email_delivery import message_parts
            self.assertEqual(message_parts(draft)[0], 'other@example.com')

    def test_forward_has_no_empty_forwarded_block(self):
        context = {'action': 'forward', 'source_id': 'AAMk1', 'recipient': '', 'cc': [], 'subject': 'Fwd: Hi',
                   'original_body': '', 'source_address': 'me@example.com'}
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='x@example.com', subject='Fwd: Hi', provider='yahoo', response_context=context)
            draft.paragraphs = [Paragraph('FYI')]
            self.assertEqual(outgoing_body(draft, 'FYI'), 'FYI')
            draft.response_context = dict(context, original_from='a@b.com', original_body='Original text')
            self.assertIn('Original text', outgoing_body(draft, 'FYI'))


class MailboxTests(unittest.TestCase):
    def test_tuesday_and_thursday_dates(self):
        self.assertNotEqual(message_date('Tue, 20 Oct 2026 10:00:00 +0000'), 'unavailable')
        self.assertNotEqual(message_date('Thu, 22 Oct 2026 10:00:00 +0000'), 'unavailable')
        self.assertNotEqual(message_date('2026-10-20T10:00:00Z'), 'unavailable')

    def test_token_refresh_does_not_switch_the_sending_account(self):
        with tempfile.TemporaryDirectory() as folder:
            store = mail_accounts.ProtectedStore(Path(folder))
            # Windows DPAPI is replaced by plain JSON for this check.
            with patch.object(mail_accounts.ProtectedStore, '_seal', staticmethod(lambda data: json.dumps(data).encode())), \
                    patch.object(mail_accounts.ProtectedStore, '_unseal', staticmethod(lambda path: json.loads(path.read_bytes()))):
                store.save('yahoo', {'address': 'me@yahoo.com'})
                store.save('thunderbird', {'address': 'me@gmail.com'}, select=False)
                self.assertEqual(store.selected(), ('yahoo', 'me@yahoo.com'))
                store.save('thunderbird', {'address': 'me@gmail.com'})
                self.assertEqual(store.selected(), ('thunderbird', 'me@gmail.com'))


class TextCleanupTests(unittest.TestCase):
    def test_period_as_a_word_is_kept(self):
        self.assertEqual(clean_dictation('The trial period is over'), 'The trial period is over')
        self.assertEqual(clean_dictation('My period of service ended'), 'My period of service ended')
        self.assertEqual(clean_dictation('hello there period how are you'), 'hello there. how are you')
        self.assertEqual(clean_dictation('Send it by friday period'), 'Send it by friday.')

    def test_control_down_from_leading_blank_lines(self):
        self.assertEqual(paragraph_destination('\n\nFirst para\nSecond para', 0, 1), 2)


if __name__ == '__main__':
    unittest.main()
